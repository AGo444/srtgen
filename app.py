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

# Store active transcription jobs
jobs = {}
job_counter = 0
job_cancel_flags = {}
active_threads = 0
active_threads_lock = threading.Lock()


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


@app.route('/api/transcribe', methods=['POST'])
def transcribe():
    """Start transcription job"""
    global job_counter
    
    data = request.json
    file_path = data.get('path')
    language = data.get('language', 'en-US')
    overwrite = data.get('overwrite', True)
    
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
    
    # Check concurrent job limit
    with active_threads_lock:
        running_jobs = sum(1 for j in jobs.values() if j['status'] == 'running')
        if running_jobs >= app.config['MAX_CONCURRENT_JOBS']:
            return jsonify({
                'error': f'Maximum concurrent jobs ({app.config["MAX_CONCURRENT_JOBS"]}) reached',
                'message': 'Please wait for current jobs to complete'
            }), 429
    
    # Create job
    job_id = job_counter
    job_counter += 1
    
    jobs[job_id] = {
        'id': job_id,
        'file': file_path,
        'language': language,
        'status': 'pending',
        'status_message': 'Waiting for available slot...',
        'progress': 0,
        'started': datetime.now().isoformat()
    }
    
    # Start transcription in background
    thread = threading.Thread(target=run_transcription, args=(job_id, full_path, language))
    thread.daemon = True
    thread.start()
    
    return jsonify({'job_id': job_id})



def run_transcription(job_id, file_path, language):
    """Run transcription in background"""
    global active_threads
    
    # Wait for available slot
    while True:
        with active_threads_lock:
            if active_threads < app.config['MAX_CONCURRENT_JOBS']:
                active_threads += 1
                break
        # Check if cancelled while waiting
        if job_cancel_flags.get(job_id, False):
            jobs[job_id]['status'] = 'cancelled'
            jobs[job_id]['status_message'] = 'Cancelled while waiting'
            return
        threading.Event().wait(1)  # Wait 1 second before checking again
    
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
        print("Transcribing in original language...")
        result_original = mkv_transcribe.transcribe_audio_whisper(audio_path, language=None, model_size=model_size)
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
            jobs[job_id]['status_message'] = 'Loading NLLB translation model...'
            
            jobs[job_id]['progress'] = 80
            jobs[job_id]['status_message'] = f'Translating to {target_lang}...'
            print(f"Translating to {target_lang} using NLLB...")
            
            # Use English SRT as source for better quality
            source_srt = srt_en if detected_lang != 'en' else srt_original
            source_lang = 'en' if detected_lang != 'en' else detected_lang
            
            translated_segments = mkv_transcribe.translate_srt_content(source_srt, source_lang, target_lang)
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
        
    except Exception as e:
        jobs[job_id]['status'] = 'failed'
        jobs[job_id]['error'] = str(e)
        import traceback
        jobs[job_id]['traceback'] = traceback.format_exc()
    finally:
        # Release thread slot
        with active_threads_lock:
            active_threads -= 1


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
    
    return jsonify({'success': True})


@app.route('/api/jobs')
def list_jobs():
    """List all jobs"""
    return jsonify(list(jobs.values()))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
