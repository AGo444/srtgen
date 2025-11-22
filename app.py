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

app = Flask(__name__)
app.config['MEDIA_FOLDER'] = os.environ.get('MEDIA_FOLDER', '/media')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 * 1024  # 16GB max file size
app.config['MAX_CONCURRENT_JOBS'] = int(os.environ.get('MAX_CONCURRENT_JOBS', '2'))
app.config['JOBS_FILE'] = '/output/jobs_queue.json'
app.config['HISTORY_FILE'] = '/output/job_history.json'

# Store active transcription jobs
jobs = {}
job_counter = 0
job_cancel_flags = {}
active_threads = 0
active_threads_lock = threading.Lock()
job_history = []


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
            
            # Reset running jobs to pending (will be restarted)
            if job_data['status'] == 'running':
                job_data['status'] = 'pending'
                job_data['progress'] = 0
            
            job_data['status_message'] = 'Recovered from restart - queued...'
            jobs[job_id] = job_data
            
            # Restart job thread
            full_path = os.path.join(app.config['MEDIA_FOLDER'], job_data['file'])
            if os.path.exists(full_path):
                thread = threading.Thread(
                    target=run_transcription, 
                    args=(job_id, full_path, job_data['language'])
                )
                thread.daemon = True
                thread.start()
            else:
                jobs[job_id]['status'] = 'failed'
                jobs[job_id]['error'] = 'File not found after restart'
        
        print(f"Recovered {len(saved_jobs)} jobs from disk")
        
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
    
    if not file_path:
        return jsonify({'error': 'No file specified'}), 400
    
    full_path = os.path.join(app.config['MEDIA_FOLDER'], file_path)
    
    if not os.path.exists(full_path):
        return jsonify({'error': 'File not found'}), 404
    
    # Check for existing SRT files
    if not overwrite:
        base_path = os.path.splitext(full_path)[0]
        existing_files = []
        
        # Check for .en.srt and language-specific .srt files
        for srt_file in [f"{base_path}.en.srt", f"{base_path}.{language[:2]}.srt"]:
            if os.path.exists(srt_file):
                existing_files.append(os.path.basename(srt_file))
        
        if existing_files:
            return jsonify({
                'error': 'Existing files found',
                'existing_files': existing_files,
                'message': 'Enable "Overwrite existing SRT files" to continue'
            }), 409
    
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
        'translation_model': translation_model
    }
    
    # Start transcription in background
    thread = threading.Thread(target=run_transcription, args=(job_id, full_path, language, whisper_model, translation_model))
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
            has_srt = any(os.path.exists(f"{base_path}.{ext}.srt") 
                         for ext in ['en', language[:2]])
            if has_srt:
                continue  # Skip files with existing SRTs
        
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
            'translation_model': translation_model
        }
        
        # Start transcription thread
        thread = threading.Thread(target=run_transcription, args=(job_id, vf['path'], language, whisper_model, translation_model))
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



def run_transcription(job_id, file_path, language, whisper_model='medium', translation_model='nllb-200-1.3B'):
    """Run transcription in background"""
    global active_threads
    
    # Wait for available slot
    while True:
        with active_threads_lock:
            if active_threads < app.config['MAX_CONCURRENT_JOBS']:
                active_threads += 1
                jobs[job_id]['status_message'] = 'Starting transcription...'
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
    
    # Import modules fresh
    import sys
    import importlib
    
    # Remove cached module if exists
    if 'mkv_transcribe' in sys.modules:
        del sys.modules['mkv_transcribe']
    
    import mkv_transcribe
    
    jobs[job_id]['status'] = 'running'
    jobs[job_id]['status_message'] = 'Starting transcription...'
    job_cancel_flags[job_id] = False
    
    try:
        # Get model size from environment variable
        model_size = os.environ.get('WHISPER_MODEL', 'medium')
        
        # Check cancel
        if job_cancel_flags.get(job_id, False):
            jobs[job_id]['status'] = 'cancelled'
            return
        
        # Extract audio
        jobs[job_id]['progress'] = 10
        jobs[job_id]['status_message'] = 'Extracting audio...'
        audio_path = mkv_transcribe.extract_audio_from_mkv(file_path)
        
        # Convert language code (nl-NL -> nl, en-US -> en)
        target_lang = language.split('-')[0] if language else 'en'
        
        base_path = os.path.splitext(file_path)[0]
        generated_files = []
        
        # Check cancel
        if job_cancel_flags.get(job_id, False):
            jobs[job_id]['status'] = 'cancelled'
            if os.path.exists(audio_path):
                os.remove(audio_path)
            return
        
        # 1. Transcribe in original language (auto-detect)
        jobs[job_id]['progress'] = 30
        jobs[job_id]['status_message'] = 'Transcribing original language...'
        print(f"Transcribing with Whisper model: {whisper_model}")
        result_original = mkv_transcribe.transcribe_audio_whisper(audio_path, language=None, model_size=whisper_model)
        detected_lang = result_original.get('language', 'unknown')
        
        srt_original = f"{base_path}.{detected_lang}.srt"
        mkv_transcribe.generate_srt_from_whisper(result_original, srt_original)
        generated_files.append(srt_original)
        print(f"✓ Generated {detected_lang} subtitles")
        
        # 2. Translate to English (always)
        if detected_lang != 'en':
            # Check cancel
            if job_cancel_flags.get(job_id, False):
                jobs[job_id]['status'] = 'cancelled'
                if os.path.exists(audio_path):
                    os.remove(audio_path)
                return
            
            jobs[job_id]['progress'] = 60
            jobs[job_id]['status_message'] = 'Translating to English...'
            print("Translating to English...")
            result_en = mkv_transcribe.translate_audio_whisper(audio_path, 'en', model_size=model_size)
            srt_en = f"{base_path}.en.srt"
            mkv_transcribe.generate_srt_from_whisper(result_en, srt_en)
            generated_files.append(srt_en)
            print("✓ Generated English subtitles")
        
        # 3. Translate to selected language using NLLB (if different from original and English)
        if target_lang not in [detected_lang, 'en']:
            # Check cancel
            if job_cancel_flags.get(job_id, False):
                jobs[job_id]['status'] = 'cancelled'
                if os.path.exists(audio_path):
                    os.remove(audio_path)
                return
            
            jobs[job_id]['progress'] = 75
            jobs[job_id]['status_message'] = f'Loading translation model ({translation_model})...'
            
            jobs[job_id]['progress'] = 80
            jobs[job_id]['status_message'] = f'Translating to {target_lang}...'
            print(f"Translating to {target_lang} using {translation_model}...")
            
            # Use English SRT as source for better quality
            source_srt = srt_en if detected_lang != 'en' else srt_original
            source_lang = 'en' if detected_lang != 'en' else detected_lang
            
            # Convert model short name to full model path
            model_path = f"facebook/{translation_model}" if not translation_model.startswith("facebook/") else translation_model
            
            translated_segments = mkv_transcribe.translate_srt_content(source_srt, source_lang, target_lang, model_path)
            srt_target = f"{base_path}.{target_lang}.srt"
            mkv_transcribe.save_translated_srt(translated_segments, srt_target)
            generated_files.append(srt_target)
            print(f"✓ Generated {target_lang} subtitles")
        
        jobs[job_id]['progress'] = 90
        
        # Clean up audio
        if os.path.exists(audio_path):
            os.remove(audio_path)
        
        jobs[job_id]['status'] = 'completed'
        jobs[job_id]['progress'] = 100
        jobs[job_id]['output'] = ', '.join(generated_files)
        jobs[job_id]['detected_language'] = detected_lang
        jobs[job_id]['generated_files'] = generated_files
        
        # Add to history
        add_to_history(jobs[job_id])
        
    except Exception as e:
        jobs[job_id]['status'] = 'failed'
        jobs[job_id]['error'] = str(e)
        import traceback
        jobs[job_id]['traceback'] = traceback.format_exc()
        
        # Add to history
        add_to_history(jobs[job_id])
        
    finally:
        # Release thread slot
        with active_threads_lock:
            active_threads -= 1
        
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
        return jsonify({'success': True})
    
    # If running, signal cancellation
    job_cancel_flags[job_id] = True
    jobs[job_id]['status'] = 'cancelled'
    jobs[job_id]['status_message'] = 'Cancelled'
    
    # Save updated state
    save_jobs_to_disk()
    
    return jsonify({'success': True})


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


if __name__ == '__main__':
    # Load saved jobs and history on startup
    load_jobs_from_disk()
    load_history_from_disk()
    
    app.run(host='0.0.0.0', port=5000, debug=False)
