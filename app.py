#!/usr/bin/env python3
"""
SRTGEN Web UI
Web interface for transcribing MKV files and managing SRT subtitles
"""

import os
import json
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
import threading
from datetime import datetime
import subprocess
from langdetect import detect
import chardet
import torch
import gc

app = Flask(__name__)
app.config['MEDIA_FOLDER'] = os.environ.get('MEDIA_FOLDER', '/media')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 * 1024  # 16GB max file size
app.config['MAX_CONCURRENT_JOBS'] = int(os.environ.get('MAX_CONCURRENT_JOBS', '2'))
app.config['WHISPER_CHUNK_LENGTH'] = int(os.environ.get('WHISPER_CHUNK_LENGTH', '30'))  # 30s chunks default
app.config['TRANSLATION_METHOD'] = os.environ.get('TRANSLATION_METHOD', 'whisper')  # whisper, nllb, nllb-whisper, or nllb-llm
app.config['OLLAMA_ENDPOINT'] = os.environ.get('OLLAMA_ENDPOINT', 'http://localhost:11434')
app.config['OLLAMA_MODEL'] = os.environ.get('OLLAMA_MODEL', 'qwen2.5:7b')
app.config['OLLAMA_TEMPERATURE'] = float(os.environ.get('OLLAMA_TEMPERATURE', '0.3'))
app.config['JOBS_FILE'] = '/output/jobs_queue.json'
app.config['HISTORY_FILE'] = '/output/job_history.json'
app.config['CONFIG_FILE'] = '/output/config.json'

# Store active transcription jobs
jobs = {}
job_counter = 0
job_cancel_flags = {}
active_threads = 0
active_threads_lock = threading.Lock()
job_history = []


def load_config_from_disk():
    """Load configuration from disk"""
    config_file = app.config['CONFIG_FILE']
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                config_data = json.load(f)
                
            if 'max_concurrent_jobs' in config_data:
                app.config['MAX_CONCURRENT_JOBS'] = int(config_data['max_concurrent_jobs'])
                print(f"⚙️ Loaded MAX_CONCURRENT_JOBS from config: {app.config['MAX_CONCURRENT_JOBS']}")
            
            if 'chunk_length' in config_data:
                app.config['WHISPER_CHUNK_LENGTH'] = int(config_data['chunk_length'])
                print(f"⚙️ Loaded WHISPER_CHUNK_LENGTH from config: {app.config['WHISPER_CHUNK_LENGTH']}")
            
            if 'translation_method' in config_data:
                app.config['TRANSLATION_METHOD'] = config_data['translation_method']
                print(f"⚙️ Loaded TRANSLATION_METHOD from config: {app.config['TRANSLATION_METHOD']}")
            
            if 'ollamaEndpoint' in config_data:
                app.config['OLLAMA_ENDPOINT'] = config_data['ollamaEndpoint']
                print(f"⚙️ Loaded OLLAMA_ENDPOINT from config: {app.config['OLLAMA_ENDPOINT']}")
            
            if 'ollamaModel' in config_data:
                app.config['OLLAMA_MODEL'] = config_data['ollamaModel']
                print(f"⚙️ Loaded OLLAMA_MODEL from config: {app.config['OLLAMA_MODEL']}")
            
            if 'ollamaTemperature' in config_data:
                app.config['OLLAMA_TEMPERATURE'] = float(config_data['ollamaTemperature'])
                print(f"⚙️ Loaded OLLAMA_TEMPERATURE from config: {app.config['OLLAMA_TEMPERATURE']}")
            
            # Return config data for frontend
            return config_data
                
        except Exception as e:
            print(f"⚠️ Error loading config: {e}")
    
    return None


def save_config_to_disk(config_data=None):
    """Save configuration to disk"""
    config_file = app.config['CONFIG_FILE']
    try:
        if config_data is None:
            config_data = {}
        
        # Merge backend settings
        config_data.update({
            'max_concurrent_jobs': app.config['MAX_CONCURRENT_JOBS'],
            'chunk_length': app.config['WHISPER_CHUNK_LENGTH'],
            'translation_method': app.config['TRANSLATION_METHOD'],
            'ollamaEndpoint': app.config['OLLAMA_ENDPOINT'],
            'ollamaModel': app.config['OLLAMA_MODEL'],
            'ollamaTemperature': app.config['OLLAMA_TEMPERATURE']
        })
        
        with open(config_file, 'w') as f:
            json.dump(config_data, f, indent=2)
            
        print(f"💾 Configuration saved to disk")
    except Exception as e:
        print(f"⚠️ Error saving config: {e}")


def cleanup_gpu_memory():
    """Aggressively clean up GPU memory with multiple passes."""
    try:
        print("🧹 Starting aggressive GPU cleanup...")
        
        # Force garbage collection 5 times (more thorough)
        for i in range(5):
            gc.collect()
        
        # Clear PyTorch CUDA cache
        if torch.cuda.is_available():
            # Empty cache multiple times
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            
            # Reset peak memory stats
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.reset_accumulated_memory_stats()
            
            # Force IPC cleanup
            try:
                torch.cuda.ipc_collect()
            except:
                pass
            
            # Empty cache again after IPC cleanup
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            
            # Get memory stats
            allocated = torch.cuda.memory_allocated() / (1024**3)  # GB
            reserved = torch.cuda.memory_reserved() / (1024**3)  # GB
            total = torch.cuda.get_device_properties(0).total_memory / (1024**3)  # GB
            free = total - allocated
            
            print(f"🧹 GPU: {allocated:.2f}GB/{total:.2f}GB used, {free:.2f}GB free, {reserved:.2f}GB reserved")
            
            # If still too much reserved but unallocated, warn user
            unallocated_reserved = reserved - allocated
            if unallocated_reserved > 1.0:  # More than 1GB reserved but unused
                print(f"⚠️ Warning: {unallocated_reserved:.2f}GB reserved but unallocated (fragmentation)")
    except Exception as e:
        print(f"⚠️ GPU cleanup warning: {e}")


def add_to_history(job_data):
    """Add completed/failed job to history"""
    global job_history
    
    history_entry = {
        'id': job_data['id'],
        'file': job_data['file'],
        'language': job_data['language'],
        'status': job_data['status'],
        'started': job_data.get('started'),
        'completed': datetime.now().isoformat(),
        'duration': None,
        'result': None,
        'error': None
    }
    
    # Calculate duration
    if job_data.get('started'):
        try:
            start_time = datetime.fromisoformat(job_data['started'])
            end_time = datetime.now()
            duration_seconds = (end_time - start_time).total_seconds()
            history_entry['duration'] = f"{int(duration_seconds // 60)}m {int(duration_seconds % 60)}s"
        except:
            pass
    
    # Add result or error
    if job_data['status'] == 'completed':
        history_entry['result'] = job_data.get('generated_files', [])
        history_entry['detected_language'] = job_data.get('detected_language')
    elif job_data['status'] == 'failed':
        history_entry['error'] = job_data.get('error', 'Unknown error')
    elif job_data['status'] == 'cancelled':
        history_entry['error'] = job_data.get('status_message', 'Job cancelled by user')
    
    job_history.insert(0, history_entry)  # Add to beginning
    
    # Keep only last 100 entries
    if len(job_history) > 100:
        job_history = job_history[:100]
    
    # Save to disk
    save_history_to_disk()


def save_history_to_disk():
    """Save history to disk"""
    try:
        os.makedirs(os.path.dirname(app.config['HISTORY_FILE']), exist_ok=True)
        with open(app.config['HISTORY_FILE'], 'w') as f:
            json.dump(job_history, f, indent=2)
    except Exception as e:
        print(f"Failed to save history to disk: {e}")


def load_history_from_disk():
    """Load history from disk"""
    global job_history
    
    try:
        if os.path.exists(app.config['HISTORY_FILE']):
            with open(app.config['HISTORY_FILE'], 'r') as f:
                job_history = json.load(f)
            print(f"Loaded {len(job_history)} history entries")
    except Exception as e:
        print(f"Failed to load history from disk: {e}")
        job_history = []


def save_jobs_to_disk():
    """Save pending/running jobs to disk for restart recovery"""
    try:
        # Only save pending and running jobs
        jobs_to_save = {
            job_id: {
                'id': job['id'],
                'file': job['file'],
                'language': job['language'],
                'status': job['status'],
                'progress': job.get('progress', 0),
                'started': job.get('started'),
            }
            for job_id, job in jobs.items()
            if job['status'] in ['pending', 'running']
        }
        
        os.makedirs(os.path.dirname(app.config['JOBS_FILE']), exist_ok=True)
        with open(app.config['JOBS_FILE'], 'w') as f:
            json.dump({
                'jobs': jobs_to_save,
                'job_counter': job_counter
            }, f, indent=2)
    except Exception as e:
        print(f"Failed to save jobs to disk: {e}")


def load_jobs_from_disk():
    """Load jobs from disk and restart pending/running jobs"""
    global jobs, job_counter
    
    try:
        if not os.path.exists(app.config['JOBS_FILE']):
            return
        
        with open(app.config['JOBS_FILE'], 'r') as f:
            data = json.load(f)
        
        job_counter = data.get('job_counter', 0)
        saved_jobs = data.get('jobs', {})
        
        # Restore jobs
        for job_id_str, job_data in saved_jobs.items():
            job_id = int(job_id_str)
            
            # Skip cancelled jobs - they should not be restarted
            if job_data['status'] == 'cancelled':
                print(f"⏭️  Skipping cancelled job {job_id}: {job_data['file']}")
                continue
            
            # Skip completed/failed jobs - they don't need to restart
            if job_data['status'] in ['completed', 'failed']:
                jobs[job_id] = job_data
                continue
            
            # Reset running jobs to pending (will be restarted)
            if job_data['status'] == 'running':
                job_data['status'] = 'pending'
                job_data['progress'] = 0
            
            job_data['status_message'] = 'Recovered from restart - queued...'
            jobs[job_id] = job_data
            
            # Restart job thread (will respect MAX_CONCURRENT_JOBS queue)
            # Ensure file is a string, not a dict
            file_path = job_data['file']
            if isinstance(file_path, dict):
                file_path = file_path.get('path') or file_path.get('name') or str(file_path)
            
            full_path = os.path.join(app.config['MEDIA_FOLDER'], file_path)
            if os.path.exists(full_path):
                # Get job parameters with defaults
                whisper_model = job_data.get('whisper_model', 'medium')
                translation_model = job_data.get('translation_model', 'nllb-200-1.3B')
                translation_method = job_data.get('translation_method', 'whisper')
                chunk_length = job_data.get('chunk_length', 30)
                overwrite = job_data.get('overwrite', True)
                
                # Start thread - it will wait in queue if needed
                thread = threading.Thread(
                    target=run_transcription, 
                    args=(job_id, full_path, job_data['language'], whisper_model, translation_model, translation_method, chunk_length, overwrite),
                    daemon=True
                )
                thread.start()
                print(f"🔄 Queued job {job_id}: {job_data['file']}")
            else:
                jobs[job_id]['status'] = 'failed'
                jobs[job_id]['error'] = 'File not found after restart'
        
        active_jobs = sum(1 for j in jobs.values() if j['status'] in ['pending', 'running'])
        print(f"Recovered {active_jobs} active jobs from disk (skipped cancelled/completed)")
        
    except Exception as e:
        print(f"Failed to load jobs from disk: {e}")


def detect_language(text):
    """Detect language from text sample"""
    try:
        return detect(text)
    except:
        return 'unknown'


def read_srt_file(srt_path):
    """Read SRT file and detect language"""
    try:
        # Detect encoding
        with open(srt_path, 'rb') as f:
            raw_data = f.read()
            result = chardet.detect(raw_data)
            encoding = result['encoding'] or 'utf-8'
        
        # Read content
        with open(srt_path, 'r', encoding=encoding, errors='ignore') as f:
            content = f.read()
        
        # Extract language from filename (e.g., video.en.srt)
        filename = os.path.basename(srt_path)
        parts = filename.rsplit('.', 2)
        language = 'unknown'
        if len(parts) >= 3 and len(parts[-2]) == 2:
            # Likely language code (2 chars before .srt)
            language = parts[-2]
        else:
            # Try to detect from content
            lines = content.split('\n')
            text_lines = []
            for line in lines:
                line = line.strip()
                if line and not line.isdigit() and '-->' not in line:
                    text_lines.append(line)
            
            sample_text = ' '.join(text_lines[:20])
            try:
                language = detect_language(sample_text)
            except:
                pass
        
        return {
            'exists': True,
            'language': language,
            'size': os.path.getsize(srt_path),
            'modified': datetime.fromtimestamp(os.path.getmtime(srt_path)).isoformat()
        }
    except Exception as e:
        return {'exists': False, 'error': str(e)}


def scan_directory(path, base_path):
    """Recursively scan directory for MKV files"""
    items = []
    
    try:
        for entry in sorted(os.listdir(path)):
            full_path = os.path.join(path, entry)
            rel_path = os.path.relpath(full_path, base_path)
            
            if os.path.isdir(full_path):
                # Directory
                items.append({
                    'name': entry,
                    'path': rel_path,
                    'type': 'directory',
                    'size': 0
                })
            elif entry.lower().endswith(('.mkv', '.mp4', '.avi')):
                # Video file
                # Check for language-specific SRT files (e.g., video.en.srt, video.nl.srt)
                base_name = os.path.splitext(full_path)[0]
                
                # Look for any .XX.srt files
                srt_files = []
                parent_dir = os.path.dirname(full_path)
                for srt_file in os.listdir(parent_dir):
                    if srt_file.startswith(os.path.splitext(entry)[0]) and srt_file.endswith('.srt'):
                        srt_files.append(os.path.join(parent_dir, srt_file))
                
                # Aggregate SRT info
                srt_info = {'exists': False, 'languages': []}
                if srt_files:
                    srt_info['exists'] = True
                    for srt_path in srt_files:
                        info = read_srt_file(srt_path)
                        if info.get('exists'):
                            srt_info['languages'].append(info.get('language', 'unknown'))
                
                items.append({
                    'name': entry,
                    'path': rel_path,
                    'type': 'video',
                    'size': os.path.getsize(full_path),
                    'modified': datetime.fromtimestamp(os.path.getmtime(full_path)).isoformat(),
                    'srt': srt_info
                })
    except PermissionError:
        pass
    
    return items


@app.route('/')
def index():
    """Main page"""
    return render_template('index.html')


@app.route('/api/browse')
def browse():
    """Browse media directory"""
    path = request.args.get('path', '')
    full_path = os.path.join(app.config['MEDIA_FOLDER'], path)
    
    if not os.path.exists(full_path) or not full_path.startswith(app.config['MEDIA_FOLDER']):
        return jsonify({'error': 'Invalid path'}), 400
    
    items = scan_directory(full_path, app.config['MEDIA_FOLDER'])
    
    return jsonify({
        'current_path': path,
        'items': items
    })


@app.route('/api/browse/scan', methods=['POST'])
def scan_folder():
    """Scan folder for video files (with optional recursion)"""
    data = request.json
    folder_path = data.get('folder', '')
    recursive = data.get('recursive', False)
    
    if not folder_path:
        return jsonify({'error': 'No folder specified'}), 400
    
    full_path = os.path.join(app.config['MEDIA_FOLDER'], folder_path)
    
    if not os.path.exists(full_path):
        return jsonify({'error': 'Folder not found'}), 404
    
    if not os.path.isdir(full_path):
        return jsonify({'error': 'Path is not a directory'}), 400
    
    video_files = []
    
    try:
        if recursive:
            # Recursive scan
            for root, dirs, files in os.walk(full_path):
                for file in sorted(files):
                    if file.lower().endswith(('.mkv', '.mp4', '.avi')):
                        file_full_path = os.path.join(root, file)
                        rel_path = os.path.relpath(file_full_path, app.config['MEDIA_FOLDER'])
                        
                        # Check for existing SRT files
                        base_path = os.path.splitext(file_full_path)[0]
                        has_srt = any(os.path.exists(f"{base_path}.{ext}.srt") 
                                    for ext in ['en', 'nl', 'de', 'fr', 'es', 'it', 'pt'])
                        
                        video_files.append({
                            'name': file,
                            'path': rel_path,
                            'full_path': file_full_path,
                            'has_srt': has_srt
                        })
        else:
            # Single directory scan
            for file in sorted(os.listdir(full_path)):
                file_full_path = os.path.join(full_path, file)
                if os.path.isfile(file_full_path) and file.lower().endswith(('.mkv', '.mp4', '.avi')):
                    rel_path = os.path.relpath(file_full_path, app.config['MEDIA_FOLDER'])
                    
                    # Check for existing SRT files
                    base_path = os.path.splitext(file_full_path)[0]
                    has_srt = any(os.path.exists(f"{base_path}.{ext}.srt") 
                                for ext in ['en', 'nl', 'de', 'fr', 'es', 'it', 'pt'])
                    
                    video_files.append({
                        'name': file,
                        'path': rel_path,
                        'full_path': file_full_path,
                        'has_srt': has_srt
                    })
        
        return jsonify({
            'files': video_files,
            'count': len(video_files)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/missing-srt', methods=['POST'])
def find_missing_srt():
    """Find all video files missing target language SRT"""
    data = request.json
    target_language = data.get('language', 'nl-NL')
    start_path = data.get('path', '')  # Optional starting directory
    
    # Convert language code (nl-NL -> nl)
    target_lang_code = target_language.split('-')[0] if target_language else 'nl'
    
    # Determine scan directory
    if start_path:
        scan_dir = os.path.join(app.config['MEDIA_FOLDER'], start_path)
        if not os.path.exists(scan_dir) or not scan_dir.startswith(app.config['MEDIA_FOLDER']):
            return jsonify({'error': 'Invalid path'}), 400
        if not os.path.isdir(scan_dir):
            return jsonify({'error': 'Path is not a directory'}), 400
    else:
        scan_dir = app.config['MEDIA_FOLDER']
    
    missing_files = []
    
    try:
        # Scan from specified directory recursively
        for root, dirs, files in os.walk(scan_dir):
            for file in sorted(files):
                if file.lower().endswith(('.mkv', '.mp4', '.avi')):
                    file_full_path = os.path.join(root, file)
                    base_path = os.path.splitext(file_full_path)[0]
                    target_srt = f"{base_path}.{target_lang_code}.srt"
                    
                    # Check if target SRT is missing
                    if not os.path.exists(target_srt):
                        rel_path = os.path.relpath(file_full_path, app.config['MEDIA_FOLDER'])
                        
                        # Check if English SRT exists (useful info)
                        has_en_srt = os.path.exists(f"{base_path}.en.srt")
                        
                        missing_files.append({
                            'name': file,
                            'path': rel_path,
                            'full_path': file_full_path,
                            'has_en_srt': has_en_srt,
                            'folder': os.path.dirname(rel_path)
                        })
        
        return jsonify({
            'files': missing_files,
            'count': len(missing_files),
            'language': target_language,
            'language_code': target_lang_code,
            'scan_path': start_path or '/'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/transcribe', methods=['POST'])
def transcribe():
    """Start transcription job"""
    global job_counter
    
    data = request.json
    file_path = data.get('path')
    language = data.get('language', 'en-US')
    overwrite = data.get('overwrite', True)
    whisper_model = data.get('whisper_model', os.environ.get('WHISPER_MODEL', 'medium'))
    translation_model = data.get('translation_model', 'nllb-200-1.3B')
    translation_method = data.get('translation_method', app.config['TRANSLATION_METHOD'])
    chunk_length = data.get('chunk_length', app.config['WHISPER_CHUNK_LENGTH'])
    
    if not file_path:
        return jsonify({'error': 'No file specified'}), 400
    
    full_path = os.path.join(app.config['MEDIA_FOLDER'], file_path)
    
    if not os.path.exists(full_path):
        return jsonify({'error': 'File not found'}), 404
    
    # Create job (always accept, queue management happens in thread)
    job_id = job_counter
    job_counter += 1
    
    # Check if queue will be full
    running_count = sum(1 for j in jobs.values() if j['status'] in ['running', 'pending'])
    queue_position = max(0, running_count - app.config['MAX_CONCURRENT_JOBS'] + 1)
    
    jobs[job_id] = {
        'id': job_id,
        'file': file_path,
        'language': language,
        'status': 'pending',
        'status_message': f'In queue (position {queue_position})...' if queue_position > 0 else 'Starting...',
        'progress': 0,
        'started': datetime.now().isoformat(),
        'whisper_model': whisper_model,
        'translation_model': translation_model,
        'translation_method': translation_method,
        'chunk_length': chunk_length,
        'overwrite': overwrite
    }
    
    # Start transcription in background
    thread = threading.Thread(target=run_transcription, args=(job_id, full_path, language, whisper_model, translation_model, translation_method, chunk_length, overwrite))
    thread.daemon = True
    thread.start()
    
    # Save jobs to disk
    save_jobs_to_disk()
    
    return jsonify({'job_id': job_id})


@app.route('/api/transcribe/batch', methods=['POST'])
def transcribe_batch():
    """Start batch transcription for specific files"""
    global job_counter
    
    data = request.json
    file_paths = data.get('files', [])
    language = data.get('language', 'en-US')
    overwrite = data.get('overwrite', True)
    whisper_model = data.get('whisper_model', os.environ.get('WHISPER_MODEL', 'medium'))
    translation_model = data.get('translation_model', 'nllb-200-1.3B')
    translation_method = data.get('translation_method', app.config['TRANSLATION_METHOD'])
    chunk_length = data.get('chunk_length', app.config['WHISPER_CHUNK_LENGTH'])
    
    if not file_paths:
        return jsonify({'error': 'No files specified'}), 400
    
    # Validate all files exist and collect info
    video_files = []
    for rel_path in file_paths:
        full_path = os.path.join(app.config['MEDIA_FOLDER'], rel_path)
        
        if not os.path.exists(full_path):
            return jsonify({'error': f'File not found: {rel_path}'}), 404
        
        if not os.path.isfile(full_path):
            return jsonify({'error': f'Not a file: {rel_path}'}), 400
        
        # Check for existing SRT files if overwrite is False
        if not overwrite:
            base_path = os.path.splitext(full_path)[0]
            # Only check for target language SRT, not source (EN) SRT
            target_lang_code = language[:2]  # e.g., 'nl' from 'nl-NL'
            target_srt = f"{base_path}.{target_lang_code}.srt"
            if os.path.exists(target_srt):
                continue  # Skip files with existing target language SRTs
        
        video_files.append({
            'path': full_path,
            'name': os.path.basename(full_path),
            'rel_path': rel_path
        })
    
    if not video_files:
        return jsonify({'error': 'No files to process (all have existing subtitles or overwrite disabled)'}), 409
    
    # Create jobs for all files
    created_jobs = []
    for vf in video_files:
        job_id = job_counter
        job_counter += 1
        
        jobs[job_id] = {
            'id': job_id,
            'file': vf['rel_path'],
            'language': language,
            'status': 'pending',
            'status_message': 'In queue...',
            'progress': 0,
            'started': datetime.now().isoformat(),
            'whisper_model': whisper_model,
            'translation_model': translation_model,
            'translation_method': translation_method,
            'chunk_length': chunk_length,
            'overwrite': overwrite
        }
        
        # Start transcription thread
        thread = threading.Thread(target=run_transcription, args=(job_id, vf['path'], language, whisper_model, translation_model, translation_method, chunk_length, overwrite))
        thread.daemon = True
        thread.start()
        
        created_jobs.append({
            'job_id': job_id,
            'file': vf['name']
        })
    
    # Save jobs to disk
    save_jobs_to_disk()
    
    return jsonify({
        'message': f'Created {len(created_jobs)} transcription jobs',
        'count': len(created_jobs),
        'jobs': created_jobs
    })


def align_nllb_with_whisper(nllb_segments, whisper_result):
    """
    Align NLLB translated text with Whisper word-level timestamps.
    Takes NLLB translation (better text quality) and uses Whisper word timestamps for better sync.
    """
    if not whisper_result.get('segments'):
        # Fallback: return NLLB segments as-is
        return nllb_segments
    
    whisper_segments = whisper_result['segments']
    aligned_segments = []
    
    # Helper to format timestamp in SRT format
    def format_srt_time(seconds):
        hours = int(seconds // 3600)
        seconds %= 3600
        minutes = int(seconds // 60)
        secs = seconds % 60
        milliseconds = int((secs % 1) * 1000)
        secs = int(secs)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"
    
    # Try to match NLLB segments with Whisper segments
    for i, nllb_seg in enumerate(nllb_segments):
        # Find closest Whisper segment by index
        if i < len(whisper_segments):
            whisper_seg = whisper_segments[i]
            
            # Extract start/end times from Whisper segment
            start_time = whisper_seg.get('start', 0.0)
            end_time = whisper_seg.get('end', start_time + 2.0)
            
            # Format as SRT timestamp
            timestamp = f"{format_srt_time(start_time)} --> {format_srt_time(end_time)}"
            
            # Use Whisper's timestamps but NLLB's text
            aligned_segments.append({
                'index': nllb_seg['index'],
                'timestamp': timestamp,
                'text': nllb_seg['text']  # Keep NLLB translation
            })
        else:
            # No matching Whisper segment, keep NLLB as-is
            aligned_segments.append(nllb_seg)
    
    return aligned_segments


def run_transcription(job_id, file_path, language, whisper_model='medium', translation_model='nllb-200-1.3B', translation_method='whisper', chunk_length=30, overwrite=True):
    """Run transcription in background"""
    global active_threads
    
    # Mark as pending initially
    jobs[job_id]['status'] = 'pending'
    
    # Wait for available slot
    while True:
        with active_threads_lock:
            if active_threads < app.config['MAX_CONCURRENT_JOBS']:
                active_threads += 1
                jobs[job_id]['status'] = 'running'
                jobs[job_id]['status_message'] = 'Starting transcription...'
                print(f"🎬 Starting job {job_id} (active threads: {active_threads}/{app.config['MAX_CONCURRENT_JOBS']})")
                break
        
        # Update queue position while waiting
        with active_threads_lock:
            running_count = sum(1 for j in jobs.values() if j['status'] == 'running')
            waiting_jobs = [j for j in jobs.values() if j['status'] == 'pending' and j['id'] < job_id]
            queue_position = len(waiting_jobs) + max(0, running_count - app.config['MAX_CONCURRENT_JOBS'] + 1)
            jobs[job_id]['status_message'] = f'In queue (position {queue_position})...'
        
        # Check if cancelled while waiting
        if job_cancel_flags.get(job_id, False):
            jobs[job_id]['status'] = 'cancelled'
            jobs[job_id]['status_message'] = 'Cancelled while in queue'
            return
        
        threading.Event().wait(2)  # Check every 2 seconds
    
    # Import mkv_transcribe module
    import mkv_transcribe
    
    job_cancel_flags[job_id] = False
    
    # Progress callback for model downloads
    def download_progress(percent, speed_mbps, eta):
        if job_id in jobs:
            jobs[job_id]['status_message'] = f'Downloading model: {percent:.1f}% @ {speed_mbps:.1f} Mbps (ETA: {eta})'
    
    try:
        # Get model size from environment variable
        model_size = os.environ.get('WHISPER_MODEL', 'medium')
        
        # Check cancel
        if job_cancel_flags.get(job_id, False):
            jobs[job_id]['status'] = 'cancelled'
            return
        
        # Extract audio
        jobs[job_id]['progress'] = 10
        jobs[job_id]['status_message'] = 'Extracting audio'
        audio_path = mkv_transcribe.extract_audio_from_mkv(file_path)
        
        # Convert language code (nl-NL -> nl, en-US -> en)
        target_lang = language.split('-')[0] if language else 'en'
        
        base_path = os.path.splitext(file_path)[0]
        generated_files = []
        
        # Check if English SRT already exists (for optimization with NLLB methods)
        srt_en = f"{base_path}.en.srt"
        has_en_srt = os.path.exists(srt_en)
        
        # Skip initial transcription if:
        # 1. English SRT already exists
        # 2. Translation method is NLLB-based (doesn't need original audio transcription)
        # 3. Target language is not English
        skip_initial_transcription = has_en_srt and translation_method in ['nllb', 'nllb-whisper', 'nllb-llm'] and target_lang != 'en'
        
        detected_lang = 'en'  # Assume English if we're skipping transcription
        srt_original = None
        
        if skip_initial_transcription:
            print(f"⚡ Skipping transcription - using existing {srt_en} for NLLB translation")
            jobs[job_id]['progress'] = 30
            jobs[job_id]['status_message'] = 'Using existing English SRT'
        else:
            # Check cancel
            if job_cancel_flags.get(job_id, False):
                jobs[job_id]['status'] = 'cancelled'
                if os.path.exists(audio_path):
                    os.remove(audio_path)
                return
            
            # 1. Transcribe in original language (auto-detect)
            jobs[job_id]['progress'] = 30
            jobs[job_id]['status_message'] = f'Transcribing ({whisper_model} model)'
            print(f"Transcribing with Whisper model: {whisper_model}, chunk_length: {chunk_length}s")
            result_original = mkv_transcribe.transcribe_audio_whisper(audio_path, language=None, model_size=whisper_model, chunk_length=chunk_length)
            detected_lang = result_original.get('language', 'unknown')
            
            srt_original = f"{base_path}.{detected_lang}.srt"
            
            # Skip if exists and overwrite is disabled
            if not overwrite and os.path.exists(srt_original):
                print(f"⏭️  Skipping {detected_lang} subtitles (already exists)")
            else:
                mkv_transcribe.generate_srt_from_whisper(result_original, srt_original)
                generated_files.append(srt_original)
                print(f"✓ Generated {detected_lang} subtitles")
        
        # 2. Translate to English (method depends on translation_method setting)
        if not has_en_srt and detected_lang != 'en':
            # Need to generate English SRT
            # Check cancel
            if job_cancel_flags.get(job_id, False):
                jobs[job_id]['status'] = 'cancelled'
                if os.path.exists(audio_path):
                    os.remove(audio_path)
                return
            
            jobs[job_id]['progress'] = 60
            jobs[job_id]['status_message'] = f'Translating to English ({translation_method})'
            
            # NLLB methods require original SRT to exist
            if translation_method in ['nllb', 'nllb-whisper', 'nllb-llm'] and not os.path.exists(srt_original):
                print(f"⚠️  NLLB method requires original SRT - using Whisper fallback")
                translation_method = 'whisper'
            
            print(f"Translating to English using method: {translation_method}...")
            
            if translation_method == 'whisper':
                # Audio-based translation with Whisper (most accurate timing)
                result_en = mkv_transcribe.translate_audio_whisper(audio_path, 'en', model_size=whisper_model, chunk_length=chunk_length)
                mkv_transcribe.generate_srt_from_whisper(result_en, srt_en)
                
            elif translation_method == 'nllb' or translation_method == 'nllb-llm':
                # Text-based translation with NLLB only (faster, less memory)
                # Requires original SRT to exist
                model_path = f"facebook/{translation_model}" if not translation_model.startswith("facebook/") else translation_model
                use_llm = translation_method == 'nllb-llm'
                translated_segments = mkv_transcribe.translate_srt_content(
                    srt_original, detected_lang, 'en', model_path, clear_model=False, 
                    progress_callback=download_progress,
                    use_llm_refinement=use_llm,
                    ollama_endpoint=app.config['OLLAMA_ENDPOINT'],
                    ollama_model=app.config['OLLAMA_MODEL'],
                    ollama_temperature=app.config['OLLAMA_TEMPERATURE']
                )
                mkv_transcribe.save_translated_srt(translated_segments, srt_en)
                
            elif translation_method == 'nllb-whisper':
                # Hybrid: NLLB translation + Whisper word-level timestamps for alignment
                # Requires original SRT to exist
                print("📝 Translating text with NLLB...")
                model_path = f"facebook/{translation_model}" if not translation_model.startswith("facebook/") else translation_model
                translated_segments = mkv_transcribe.translate_srt_content(srt_original, detected_lang, 'en', model_path, clear_model=True, progress_callback=download_progress)
                
                # Force aggressive GPU cleanup before loading Whisper
                print("🧹 Clearing GPU memory before Whisper...")
                cleanup_gpu_memory()
                
                print("🎙️ Getting word-level timestamps from Whisper...")
                result_en = mkv_transcribe.translate_audio_whisper(audio_path, 'en', model_size=whisper_model, chunk_length=chunk_length)
                
                # Align NLLB translation with Whisper timestamps
                print("🔗 Aligning NLLB text with Whisper timestamps...")
                aligned_segments = align_nllb_with_whisper(translated_segments, result_en)
                mkv_transcribe.save_translated_srt(aligned_segments, srt_en)
            
            generated_files.append(srt_en)
            print("✓ Generated English subtitles")
        elif has_en_srt:
            print(f"⏭️  Using existing English subtitles: {srt_en}")
        else:
            print(f"⏭️  Skipping English subtitles (source is already English)")
        
        # 3. Translate to selected language using NLLB (if different from original and English)
        if target_lang not in [detected_lang, 'en']:
            srt_target = f"{base_path}.{target_lang}.srt"
            
            # Check if target language SRT already exists
            if not overwrite and os.path.exists(srt_target):
                print(f"⏭️  Skipping {target_lang} subtitles (already exists)")
            else:
                # Check cancel
                if job_cancel_flags.get(job_id, False):
                    jobs[job_id]['status'] = 'cancelled'
                    if os.path.exists(audio_path):
                        os.remove(audio_path)
                    return
                
                # Use translation_method for target language translation too
                if translation_method == 'nllb-whisper' or translation_method == 'nllb-llm':
                    # Hybrid method: NLLB translation + Whisper timing alignment (+ optional LLM refinement)
                    jobs[job_id]['progress'] = 75
                    method_name = 'NLLB+LLM' if translation_method == 'nllb-llm' else 'NLLB'
                    jobs[job_id]['status_message'] = f'Translating to {target_lang.upper()} with {method_name}'
                    
                    print(f"📝 Translating EN → {target_lang} with {method_name}...")
                    model_path = f"facebook/{translation_model}" if not translation_model.startswith("facebook/") else translation_model
                    use_llm = translation_method == 'nllb-llm'
                    translated_segments = mkv_transcribe.translate_srt_content(
                        srt_en, 'en', target_lang, model_path, clear_model=True, 
                        progress_callback=download_progress,
                        use_llm_refinement=use_llm,
                        ollama_endpoint=app.config['OLLAMA_ENDPOINT'],
                        ollama_model=app.config['OLLAMA_MODEL'],
                        ollama_temperature=app.config['OLLAMA_TEMPERATURE']
                    )
                    
                    # Force aggressive GPU cleanup before loading Whisper
                    print("🧹 Clearing GPU memory before Whisper...")
                    cleanup_gpu_memory()
                    
                    jobs[job_id]['progress'] = 85
                    jobs[job_id]['status_message'] = f'Getting timing with Whisper ({whisper_model})'
                    
                    print(f"🎙️ Getting word-level timestamps from Whisper for {target_lang}...")
                    result_target = mkv_transcribe.translate_audio_whisper(audio_path, target_lang, model_size=whisper_model, chunk_length=chunk_length)
                    
                    print(f"🔗 Aligning NLLB {target_lang} text with Whisper timestamps...")
                    aligned_segments = align_nllb_with_whisper(translated_segments, result_target)
                    mkv_transcribe.save_translated_srt(aligned_segments, srt_target)
                    
                else:
                    # Pure NLLB or Whisper method
                    jobs[job_id]['progress'] = 75
                    jobs[job_id]['status_message'] = f'Loading {translation_model}'
                    
                    jobs[job_id]['progress'] = 80
                    jobs[job_id]['status_message'] = f'Translating to {target_lang.upper()}'
                    print(f"Translating to {target_lang} using {translation_model}...")
                    
                    # Use English SRT as source for better quality
                    source_srt = srt_en if os.path.exists(srt_en) else srt_original
                    source_lang = 'en' if os.path.exists(srt_en) else detected_lang
                    
                    # Convert model short name to full model path
                    model_path = f"facebook/{translation_model}" if not translation_model.startswith("facebook/") else translation_model
                    
                    # Check if we should use LLM refinement
                    use_llm = translation_method == 'nllb-llm'
                    translated_segments = mkv_transcribe.translate_srt_content(
                        source_srt, source_lang, target_lang, model_path, 
                        progress_callback=download_progress,
                        use_llm_refinement=use_llm,
                        ollama_endpoint=app.config['OLLAMA_ENDPOINT'],
                        ollama_model=app.config['OLLAMA_MODEL'],
                        ollama_temperature=app.config['OLLAMA_TEMPERATURE']
                    )
                    mkv_transcribe.save_translated_srt(translated_segments, srt_target)
                
                generated_files.append(srt_target)
                print(f"✓ Generated {target_lang} subtitles")
        
        jobs[job_id]['progress'] = 90
        jobs[job_id]['status_message'] = 'Cleaning up'
        
        # Clean up audio
        if os.path.exists(audio_path):
            os.remove(audio_path)
        
        jobs[job_id]['status'] = 'completed'
        jobs[job_id]['progress'] = 100
        jobs[job_id]['status_message'] = 'Complete'
        jobs[job_id]['output'] = ', '.join(generated_files)
        jobs[job_id]['detected_language'] = detected_lang
        jobs[job_id]['generated_files'] = generated_files
        
        # Clear NLLB model explicitly
        try:
            mkv_transcribe.clear_nllb_model()
        except:
            pass
        
        # Clear GPU memory after job completes
        cleanup_gpu_memory()
        
        # Add to history
        add_to_history(jobs[job_id])
        
    except Exception as e:
        jobs[job_id]['status'] = 'failed'
        error_msg = str(e)
        
        # Add helpful tip for GPU memory errors
        if 'CUDA out of memory' in error_msg or 'out of memory' in error_msg.lower():
            current_max = app.config['MAX_CONCURRENT_JOBS']
            current_chunk = app.config.get('WHISPER_CHUNK_LENGTH', 30)
            error_msg += f"\n\n💡 TIP: You're running {current_max} concurrent job(s). Try:\n"
            error_msg += "  • Lower 'Max Concurrent Jobs' to 1 in Settings ⚙️\n"
            error_msg += f"  • Lower 'Whisper Chunk Length' to 15-20s (currently {current_chunk}s)\n"
            error_msg += "  • Use smaller models: Medium + 1.3B instead of Large + 3.3B\n"
            error_msg += f"  • Current GPU has only ~15GB VRAM available\n"
            error_msg += "  • Check for other processes using the NVIDIA card: nvidia-smi"
        
        jobs[job_id]['error'] = error_msg
        import traceback
        jobs[job_id]['traceback'] = traceback.format_exc()
        
        # Clear NLLB model even on failure
        try:
            mkv_transcribe.clear_nllb_model()
        except:
            pass
        
        # Clear GPU memory even on failure
        cleanup_gpu_memory()
        
        # Add to history
        add_to_history(jobs[job_id])
        
    finally:
        # Clear NLLB model to free memory
        try:
            mkv_transcribe.clear_nllb_model()
        except:
            pass
        
        # Release thread slot
        with active_threads_lock:
            active_threads -= 1
        
        # Final aggressive cleanup before releasing thread
        cleanup_gpu_memory()
        
        # Give GPU time to stabilize
        import time
        time.sleep(2)
        
        # Save updated job state to disk
        save_jobs_to_disk()


@app.route('/api/jobs/<int:job_id>')
def get_job(job_id):
    """Get job status"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    return jsonify(jobs[job_id])


@app.route('/api/jobs/<int:job_id>/cancel', methods=['POST'])
def cancel_job(job_id):
    """Cancel a running job"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    if jobs[job_id]['status'] in ['completed', 'failed', 'cancelled']:
        return jsonify({'error': 'Job already finished'}), 400
    
    # If still pending, cancel immediately
    if jobs[job_id]['status'] == 'pending':
        jobs[job_id]['status'] = 'cancelled'
        jobs[job_id]['status_message'] = 'Cancelled before start'
        job_cancel_flags[job_id] = True
        
        # Add to history
        add_to_history(jobs[job_id])
        
        return jsonify({'success': True})
    
    # If running, signal cancellation
    job_cancel_flags[job_id] = True
    jobs[job_id]['status'] = 'cancelled'
    jobs[job_id]['status_message'] = 'Cancelled'
    
    # Add to history
    add_to_history(jobs[job_id])
    
    # Save updated state
    save_jobs_to_disk()
    
    return jsonify({'success': True})


@app.route('/api/jobs/<int:job_id>/bump', methods=['POST'])
def bump_job(job_id):
    """Move a pending job to the front of the queue"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    if jobs[job_id]['status'] != 'pending':
        return jsonify({'error': 'Only pending jobs can be bumped'}), 400
    
    # Find the lowest job ID among pending jobs (excluding this one)
    pending_jobs = [j for j in jobs.values() if j['status'] == 'pending' and j['id'] != job_id]
    
    if not pending_jobs:
        return jsonify({'message': 'Already at front of queue'}), 200
    
    # Get the minimum job ID (first in queue)
    min_job_id = min(j['id'] for j in pending_jobs)
    
    # Swap IDs: give this job a lower ID than the current first job
    new_job_id = min_job_id - 1
    
    # Update job with new ID
    job_data = jobs[job_id]
    del jobs[job_id]
    job_data['id'] = new_job_id
    jobs[new_job_id] = job_data
    
    # Update cancel flags
    if job_id in job_cancel_flags:
        job_cancel_flags[new_job_id] = job_cancel_flags[job_id]
        del job_cancel_flags[job_id]
    
    # Save updated state
    save_jobs_to_disk()
    
    print(f"📌 Job {job_id} bumped to position with ID {new_job_id}")
    
    return jsonify({'success': True, 'new_id': new_job_id})


@app.route('/api/jobs')
def list_jobs():
    """List all jobs"""
    return jsonify(list(jobs.values()))


@app.route('/api/history')
def get_history():
    """Get job history"""
    return jsonify(job_history)


@app.route('/api/history/clear', methods=['POST'])
def clear_history():
    """Clear job history"""
    global job_history
    job_history = []
    save_history_to_disk()
    return jsonify({'success': True})


@app.route('/api/history/delete-filtered', methods=['POST'])
def delete_filtered_history():
    """Delete filtered job history by status"""
    global job_history
    data = request.json
    status_filter = data.get('status', 'all')
    
    if status_filter == 'all':
        job_history = []
    else:
        job_history = [job for job in job_history if job.get('status') != status_filter]
    
    save_history_to_disk()
    return jsonify({'success': True})


@app.route('/api/translate-srt', methods=['POST'])
def translate_srt():
    """Translate an existing English SRT file to target language"""
    global job_counter
    
    data = request.json
    srt_path = data.get('srt_path')  # Relative path to English SRT
    target_language = data.get('language', 'nl')  # Default to Dutch
    translation_model = data.get('translation_model', 'nllb-200-1.3B')
    
    if not srt_path:
        return jsonify({'error': 'No SRT file specified'}), 400
    
    # Build full path
    full_srt_path = os.path.join(app.config['MEDIA_FOLDER'], srt_path)
    
    if not os.path.exists(full_srt_path):
        return jsonify({'error': f'SRT file not found: {srt_path}'}), 404
    
    if not full_srt_path.endswith('.srt'):
        return jsonify({'error': 'File must be an SRT file'}), 400
    
    # Create job
    job_id = job_counter
    job_counter += 1
    
    jobs[job_id] = {
        'id': job_id,
        'file': srt_path,
        'language': target_language,
        'status': 'pending',
        'status_message': 'In queue...',
        'progress': 0,
        'started': datetime.now().isoformat(),
        'translation_model': translation_model,
        'type': 'srt-translation'
    }
    
    save_jobs_to_disk()
    
    # Start translation thread
    thread = threading.Thread(target=run_srt_translation, args=(job_id, full_srt_path, target_language, translation_model))
    thread.start()
    
    return jsonify({'job_id': job_id, 'status': 'started'})


def run_srt_translation(job_id: int, srt_path: str, target_language: str, translation_model: str):
    """Translate an existing SRT file using NLLB"""
    global active_threads
    
    # Wait for slot
    while True:
        with active_threads_lock:
            if active_threads < app.config['MAX_CONCURRENT_JOBS']:
                active_threads += 1
                break
        import time
        time.sleep(1)
    
    try:
        jobs[job_id]['status'] = 'processing'
        jobs[job_id]['progress'] = 10
        jobs[job_id]['status_message'] = 'Loading translation model...'
        
        # Determine output path
        base_path = os.path.splitext(srt_path)[0]
        # Remove .en suffix if present
        if base_path.endswith('.en'):
            base_path = base_path[:-3]
        
        target_lang_code = target_language.split('-')[0]  # e.g., 'nl-NL' -> 'nl'
        output_srt = f"{base_path}.{target_lang_code}.srt"
        
        jobs[job_id]['progress'] = 30
        jobs[job_id]['status_message'] = f'Translating to {target_lang_code}...'
        
        # Use existing NLLB translation function
        model_name = f"facebook/nllb-200-{translation_model.split('-')[-1]}"  # nllb-200-1.3B
        translated_segments = mkv_transcribe.translate_srt_content(srt_path, 'en', target_lang_code, model_name)
        
        jobs[job_id]['progress'] = 80
        jobs[job_id]['status_message'] = 'Writing translated subtitles...'
        
        # Write translated SRT
        mkv_transcribe.save_translated_srt(translated_segments, output_srt)
        
        jobs[job_id]['status'] = 'completed'
        jobs[job_id]['progress'] = 100
        jobs[job_id]['status_message'] = f'Translation complete: {os.path.basename(output_srt)}'
        jobs[job_id]['completed'] = datetime.now().isoformat()
        jobs[job_id]['output'] = output_srt
        
        print(f"✓ SRT translation complete: {output_srt}")
        
        # Move to history
        job_history.append(jobs[job_id].copy())
        save_history_to_disk()
        
    except Exception as e:
        jobs[job_id]['status'] = 'failed'
        jobs[job_id]['status_message'] = f'Translation error: {str(e)}'
        jobs[job_id]['error'] = str(e)
        print(f"✗ SRT translation failed: {e}")
        
        # Move to history
        job_history.append(jobs[job_id].copy())
        save_history_to_disk()
    
    finally:
        with active_threads_lock:
            active_threads -= 1
        
        cleanup_gpu_memory()
        save_jobs_to_disk()


@app.route('/api/settings', methods=['GET', 'POST'])
def settings():
    """Get or update application settings"""
    if request.method == 'GET':
        # Load and return current config
        config_data = load_config_from_disk() or {}
        
        # Add current backend settings
        config_data.update({
            'max_concurrent_jobs': app.config['MAX_CONCURRENT_JOBS'],
            'chunk_length': app.config['WHISPER_CHUNK_LENGTH'],
            'translation_method': app.config['TRANSLATION_METHOD'],
            'ollamaEndpoint': app.config['OLLAMA_ENDPOINT'],
            'ollamaModel': app.config['OLLAMA_MODEL'],
            'ollamaTemperature': app.config['OLLAMA_TEMPERATURE']
        })
        
        return jsonify(config_data)
    
    # POST - Update settings
    data = request.json
    updated = {}
    
    # Backend settings
    if 'max_concurrent_jobs' in data:
        max_jobs = int(data['max_concurrent_jobs'])
        if 1 <= max_jobs <= 10:
            app.config['MAX_CONCURRENT_JOBS'] = max_jobs
            updated['max_concurrent_jobs'] = max_jobs
            print(f"⚙️ Max concurrent jobs updated to: {max_jobs}")
    
    if 'chunk_length' in data:
        chunk_length = int(data['chunk_length'])
        if chunk_length == 0 or (10 <= chunk_length <= 60):
            app.config['WHISPER_CHUNK_LENGTH'] = chunk_length
            updated['chunk_length'] = chunk_length
            print(f"⚙️ Whisper chunk length updated to: {chunk_length}s")
    
    if 'translation_method' in data:
        translation_method = data['translation_method']
        if translation_method in ['whisper', 'nllb', 'nllb-whisper', 'nllb-llm']:
            app.config['TRANSLATION_METHOD'] = translation_method
            updated['translation_method'] = translation_method
            print(f"⚙️ Translation method updated to: {translation_method}")
    
    if 'ollamaEndpoint' in data:
        app.config['OLLAMA_ENDPOINT'] = data['ollamaEndpoint']
        updated['ollamaEndpoint'] = data['ollamaEndpoint']
        print(f"⚙️ Ollama endpoint updated to: {data['ollamaEndpoint']}")
    
    if 'ollamaModel' in data:
        app.config['OLLAMA_MODEL'] = data['ollamaModel']
        updated['ollamaModel'] = data['ollamaModel']
        print(f"⚙️ Ollama model updated to: {data['ollamaModel']}")
    
    if 'ollamaTemperature' in data:
        temp = float(data['ollamaTemperature'])
        if 0.0 <= temp <= 2.0:
            app.config['OLLAMA_TEMPERATURE'] = temp
            updated['ollamaTemperature'] = temp
            print(f"⚙️ Ollama temperature updated to: {temp}")
    
    # Frontend settings (pass-through to config file)
    frontend_settings = ['defaultLanguage', 'whisperModel', 'translationModel', 'overwriteExisting', 'downloadSpeedLimit', 'downloadSpeedMBps']
    for setting in frontend_settings:
        if setting in data:
            updated[setting] = data[setting]
    
    # Update environment variable for model downloads
    if 'downloadSpeedMBps' in data and data['downloadSpeedMBps'] > 0:
        os.environ['MODEL_DOWNLOAD_SPEED_MB'] = str(int(data['downloadSpeedMBps']))
        print(f"⚙️ Model download speed set to: {data['downloadSpeedMBps']} MB/s")
    elif 'downloadSpeedMBps' in data and data['downloadSpeedMBps'] == 0:
        os.environ['MODEL_DOWNLOAD_SPEED_MB'] = '0'  # No limit
        print(f"⚙️ Model download speed: unlimited")
    
    if updated:
        # Load existing config to merge with
        existing_config = load_config_from_disk() or {}
        existing_config.update(updated)
        save_config_to_disk(existing_config)
        return jsonify({'success': True, **updated})
    
    return jsonify({'error': 'Invalid settings'}), 400


if __name__ == '__main__':
    # Load configuration, saved jobs and history on startup
    load_config_from_disk()
    load_jobs_from_disk()
    load_history_from_disk()
    
    # Only use Flask dev server when running directly (not via Gunicorn)
    app.run(host='0.0.0.0', port=5000, debug=False)
else:
    # When loaded by Gunicorn, load config, jobs and history at module level
    load_config_from_disk()
    load_jobs_from_disk()
    load_history_from_disk()

