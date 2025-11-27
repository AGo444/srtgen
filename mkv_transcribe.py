#!/usr/bin/env python3
"""
MKV Transcription Tool
Extracts audio from MKV files and transcribes to SRT using Whisper AI (GPU accelerated).
"""

import os
import sys
import argparse
import subprocess
from pathlib import Path
import whisper
import torch
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
import gc
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import requests
import ollama


# Rate limiter for downloads
class RateLimitedHTTPAdapter(HTTPAdapter):
    """HTTP adapter that limits download speed to avoid saturating network."""
    def __init__(self, max_bytes_per_sec=10*1024*1024, progress_callback=None, *args, **kwargs):
        self.max_bytes_per_sec = max_bytes_per_sec
        self.chunk_size = 8192  # 8KB chunks
        self.progress_callback = progress_callback
        self.total_downloaded = 0
        self.start_time = None
        print(f"🔧 RateLimitedHTTPAdapter initialized: max_bytes_per_sec={max_bytes_per_sec/1024/1024:.1f} MB/s")
        super().__init__(*args, **kwargs)
    
    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        print(f"📥 HTTP request: {request.method} {request.url[:100]}...")
        response = super().send(request, stream=True, timeout=timeout, verify=verify, cert=cert, proxies=proxies)
        
        # Only rate limit large downloads (model files)
        if stream and response.headers.get('content-length'):
            content_length = int(response.headers['content-length'])
            print(f"📊 Response size: {content_length/1024/1024:.1f} MB")
            if content_length > 100 * 1024 * 1024:  # Only rate limit files > 100MB
                print(f"🚀 Starting rate-limited download: {content_length/1024/1024:.1f} MB")
                self.total_downloaded = 0
                self.start_time = time.time()
                response.raw.read = self._rate_limited_read(response.raw.read, content_length)
            else:
                print(f"⚡ Small file, no rate limiting")
        
        return response
    
    def _rate_limited_read(self, original_read, total_size):
        """Wrap the read method to add rate limiting and progress tracking."""
        def wrapped_read(amt=None):
            start = time.time()
            data = original_read(amt)
            
            if data:
                self.total_downloaded += len(data)
                
                # Progress tracking
                if self.progress_callback and total_size:
                    percent = (self.total_downloaded / total_size) * 100
                    elapsed = time.time() - self.start_time
                    speed_mbps = (self.total_downloaded * 8 / 1000000) / elapsed if elapsed > 0 else 0
                    
                    # Log every 5%
                    if int(percent) % 5 == 0 and int(percent) != int((self.total_downloaded - len(data)) / total_size * 100):
                        print(f"📊 Download progress: {percent:.1f}% ({self.total_downloaded/1024/1024:.1f}/{total_size/1024/1024:.1f} MB) @ {speed_mbps:.1f} Mbps")
                    
                    # Estimate remaining time
                    if speed_mbps > 0:
                        remaining_mb = (total_size - self.total_downloaded) / (1024 * 1024)
                        eta_seconds = (remaining_mb * 8) / speed_mbps
                        eta_min = int(eta_seconds // 60)
                        eta_sec = int(eta_seconds % 60)
                        self.progress_callback(percent, speed_mbps, f"{eta_min}m {eta_sec}s")
                
                # Rate limiting
                if self.max_bytes_per_sec:
                    bytes_read = len(data)
                    expected_time = bytes_read / self.max_bytes_per_sec
                    elapsed = time.time() - start
                    sleep_time = expected_time - elapsed
                    
                    if sleep_time > 0:
                        time.sleep(sleep_time)
            
            return data
        return wrapped_read


def setup_rate_limited_downloads(max_mbps=10, progress_callback=None):
    """Setup rate-limited downloads for HuggingFace models."""
    import huggingface_hub
    
    print(f"🔧 Setting up rate-limited downloads: {max_mbps} MB/s")
    
    # Create rate-limited session
    session = requests.Session()
    
    # Convert Mbps to bytes per second
    max_bytes_per_sec = max_mbps * 1024 * 1024
    print(f"🔧 Max bytes per second: {max_bytes_per_sec:,}")
    
    adapter = RateLimitedHTTPAdapter(
        max_bytes_per_sec=max_bytes_per_sec,
        progress_callback=progress_callback,
        max_retries=Retry(total=3, backoff_factor=0.5)
    )
    
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    print(f"🔧 Session adapters mounted for http:// and https://")
    
    # Monkey patch the HuggingFace hub to use our session
    huggingface_hub.file_download._CACHED_NO_EXIST = {}  # Clear cache
    huggingface_hub.constants.HF_HUB_DOWNLOAD_TIMEOUT = 300  # 5 min timeout
    
    print(f"📊 Download rate limited to {max_mbps} MB/s to preserve network bandwidth")
    
    return session


def extract_audio_from_mkv(mkv_path: str, output_audio: str = None) -> str:
    """Extract audio from MKV file using ffmpeg."""
    if output_audio is None:
        # Use /tmp for temporary audio files instead of media folder
        import tempfile
        temp_dir = tempfile.gettempdir()
        filename = Path(mkv_path).stem + '.wav'
        output_audio = os.path.join(temp_dir, filename)
    
    print(f"Extracting audio from {mkv_path}...")
    
    cmd = [
        'ffmpeg', '-i', mkv_path, '-vn', '-acodec', 'pcm_s16le',
        '-ar', '16000', '-ac', '1', '-y', output_audio
    ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"Audio extracted to {output_audio}")
        return output_audio
    except subprocess.CalledProcessError as e:
        print(f"Error extracting audio: {e.stderr.decode()}")
        raise


def format_srt_time(seconds: float) -> str:
    """Convert seconds to SRT time format (HH:MM:SS,mmm)"""
    hours = int(seconds // 3600)
    seconds %= 3600
    minutes = int(seconds // 60)
    seconds %= 60
    milliseconds = int((seconds % 1) * 1000)
    seconds = int(seconds)
    
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def transcribe_audio_whisper(audio_path: str, language: str = None, model_size: str = "base", chunk_length: int = 30) -> dict:
    """Transcribe audio file using OpenAI Whisper with GPU acceleration."""
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")
    if device == "cuda":
        print(f"GPU: {torch.cuda.get_device_name(0)}")
    
    print(f"Loading Whisper {model_size} model...")
    model = whisper.load_model(model_size, device=device)
    
    try:
        # Map chunk_length to beam_size/best_of for memory optimization
        # Smaller values = less GPU memory, slightly lower quality
        if chunk_length <= 15:
            beam_size, best_of = 3, 3  # Lowest memory
            mode_msg = " (low memory mode)"
        elif chunk_length <= 20:
            beam_size, best_of = 4, 4  # Medium memory
            mode_msg = " (balanced mode)"
        elif chunk_length <= 30:
            beam_size, best_of = 5, 5  # Default
            mode_msg = " (standard mode)"
        else:  # chunk_length == 0 or > 30
            beam_size, best_of = 5, 5  # Full quality
            mode_msg = " (full quality mode)"
        
        print(f"Transcribing {audio_path} with word-level timestamps{mode_msg}...")
        
        result = model.transcribe(
            audio_path,
            language=language,
            verbose=True,
            word_timestamps=True,
            condition_on_previous_text=True,
            beam_size=beam_size,
            best_of=best_of,
            temperature=0.0  # Deterministic for consistency
        )
        return result
    finally:
        # AGGRESSIVE GPU memory cleanup
        print("🧹 Cleaning up Whisper model...")
        del model
        
        # Multiple garbage collection passes
        for _ in range(5):
            gc.collect()
        
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            
            # Reset memory stats
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.reset_accumulated_memory_stats()
            
            try:
                torch.cuda.ipc_collect()
            except:
                pass
            
            # Empty cache again
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            
            allocated = torch.cuda.memory_allocated() / (1024**3)
            reserved = torch.cuda.memory_reserved() / (1024**3)
            print(f"🧹 GPU after cleanup: {allocated:.2f}GB allocated, {reserved:.2f}GB reserved")


def translate_audio_whisper(audio_path: str, target_language: str, model_size: str = "base", chunk_length: int = 30) -> dict:
    """Translate audio to target language using Whisper."""
    device = "cuda" if torch.cuda.is_available() else "cpu"
    
    # Map chunk_length to beam_size/best_of for memory optimization
    if chunk_length <= 15:
        beam_size, best_of = 3, 3
        mode_msg = " (low memory mode)"
    elif chunk_length <= 20:
        beam_size, best_of = 4, 4
        mode_msg = " (balanced mode)"
    elif chunk_length <= 30:
        beam_size, best_of = 5, 5
        mode_msg = " (standard mode)"
    else:
        beam_size, best_of = 5, 5
        mode_msg = " (full quality mode)"
    
    print(f"Translating to {target_language} with word-level timestamps{mode_msg}...")
    
    model = whisper.load_model(model_size, device=device)
    
    try:
        # Whisper's task='translate' always translates to English
        result = model.transcribe(
            audio_path,
            task='translate',
            verbose=True,
            word_timestamps=True,
            condition_on_previous_text=True,
            beam_size=beam_size,
            best_of=best_of,
            temperature=0.0
        )
        return result
    finally:
        # AGGRESSIVE GPU memory cleanup
        print("🧹 Cleaning up Whisper translation model...")
        del model
        
        # Multiple garbage collection passes
        for _ in range(5):
            gc.collect()
        
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            
            # Reset memory stats
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.reset_accumulated_memory_stats()
            
            try:
                torch.cuda.ipc_collect()
            except:
                pass
            
            # Empty cache again
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            
            allocated = torch.cuda.memory_allocated() / (1024**3)
            reserved = torch.cuda.memory_reserved() / (1024**3)
            print(f"🧹 GPU after Whisper cleanup: {allocated:.2f}GB allocated, {reserved:.2f}GB reserved")


# NLLB language code mapping
NLLB_LANG_MAP = {
    'en': 'eng_Latn',
    'nl': 'nld_Latn',
    'fr': 'fra_Latn',
    'de': 'deu_Latn',
    'es': 'spa_Latn',
    'it': 'ita_Latn',
    'pt': 'por_Latn',
    'pl': 'pol_Latn',
    'ru': 'rus_Cyrl',
    'ja': 'jpn_Jpan',
    'zh': 'zho_Hans',
    'ko': 'kor_Hang',
    'ar': 'arb_Arab',
    'tr': 'tur_Latn',
    'sv': 'swe_Latn',
    'da': 'dan_Latn',
    'no': 'nob_Latn',
    'fi': 'fin_Latn',
    'cs': 'ces_Latn',
    'el': 'ell_Grek',
    'he': 'heb_Hebr',
    'hi': 'hin_Deva',
    'th': 'tha_Thai',
    'vi': 'vie_Latn',
    'id': 'ind_Latn',
    'ms': 'zsm_Latn',
    'uk': 'ukr_Cyrl',
    'ro': 'ron_Latn',
    'hu': 'hun_Latn',
    'bg': 'bul_Cyrl',
    'hr': 'hrv_Latn',
    'sk': 'slk_Latn',
}

_nllb_model = None
_nllb_tokenizer = None
_nllb_model_name = None

def get_nllb_model(model_name="facebook/nllb-200-1.3B", progress_callback=None):
    """Load NLLB model once and cache it."""
    global _nllb_model, _nllb_tokenizer, _nllb_model_name
    
    # Reload if different model requested
    if _nllb_model is None or _nllb_model_name != model_name:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"Loading {model_name} translation model on {device}...")
        
        print("⚠️  Rate limiting DISABLED for testing")
        
        print(f"🔧 Loading tokenizer from {model_name}...")
        
        # Workaround for HuggingFace transformers bug with dict chat_template
        # Download tokenizer config and fix it before loading
        from huggingface_hub import hf_hub_download
        import json
        import tempfile
        import shutil
        
        try:
            # Download original config
            config_path = hf_hub_download(repo_id=model_name, filename="tokenizer_config.json")
            print(f"🔧 Downloaded tokenizer config from {model_name}")
            
            # Read and fix config
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            # Check if chat_template is problematic
            if "chat_template" in config:
                print(f"🔧 Original chat_template type: {type(config['chat_template'])}")
                if isinstance(config['chat_template'], dict):
                    print(f"⚠️  Removing dict chat_template (causes TypeError)")
                    del config["chat_template"]
            
            # Create temp directory with fixed config
            temp_dir = tempfile.mkdtemp()
            fixed_config_path = os.path.join(temp_dir, "tokenizer_config.json")
            with open(fixed_config_path, 'w') as f:
                json.dump(config, f)
            
            # Copy other tokenizer files to temp dir
            for filename in ["sentencepiece.bpe.model", "tokenizer.json", "special_tokens_map.json"]:
                try:
                    src = hf_hub_download(repo_id=model_name, filename=filename)
                    dst = os.path.join(temp_dir, filename)
                    shutil.copy(src, dst)
                except:
                    pass  # Some files might not exist
            
            # Load tokenizer from local directory with fixed config
            _nllb_tokenizer = AutoTokenizer.from_pretrained(
                temp_dir,
                use_fast=False,
                local_files_only=True
            )
            
            # Cleanup
            shutil.rmtree(temp_dir)
            print(f"✓ Tokenizer loaded with fixed config")
            
        except Exception as e:
            print(f"⚠️  Config fix failed: {e}")
            print(f"Trying direct load...")
            _nllb_tokenizer = AutoTokenizer.from_pretrained(
                model_name,
                use_fast=False
            )
            print(f"✓ Tokenizer loaded")
        
        print(f"🔧 Loading model from {model_name}...")
        
        # Notify progress callback about model download
        if progress_callback:
            # Estimate model size for progress display
            model_sizes = {
                "facebook/nllb-200-distilled-600M": 600,
                "facebook/nllb-200-1.3B": 1300,
                "facebook/nllb-200-3.3B": 6500
            }
            size_mb = model_sizes.get(model_name, 1000)
            progress_callback(10, 0, f"Downloading NLLB model ({size_mb}MB, 10-15 min)")
        
        _nllb_model = AutoModelForSeq2SeqLM.from_pretrained(model_name).to(device)
        _nllb_model_name = model_name
        
        if progress_callback:
            progress_callback(100, 0, "Model loaded")
        
        print(f"✓ {model_name} model loaded and moved to {device}")
    
    return _nllb_model, _nllb_tokenizer


def clear_nllb_model():
    """Clear NLLB model from GPU memory with aggressive cleanup."""
    global _nllb_model, _nllb_tokenizer, _nllb_model_name
    
    if _nllb_model is not None:
        print("🧹 Clearing NLLB model from GPU...")
        del _nllb_model
        del _nllb_tokenizer
        _nllb_model = None
        _nllb_tokenizer = None
        _nllb_model_name = None
        
        # Multiple garbage collection passes
        for _ in range(5):
            gc.collect()
        
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            
            # Reset memory stats
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.reset_accumulated_memory_stats()
            
            try:
                torch.cuda.ipc_collect()
            except:
                pass
            
            # Empty cache again
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            
            allocated = torch.cuda.memory_allocated() / (1024**3)
            reserved = torch.cuda.memory_reserved() / (1024**3)
            print(f"🧹 NLLB cleared - GPU: {allocated:.2f}GB allocated, {reserved:.2f}GB reserved")


def translate_text_nllb(text: str, source_lang: str, target_lang: str, model_name="facebook/nllb-200-1.3B") -> str:
    """Translate text using NLLB-200 model."""
    if not text or not text.strip():
        return text
    
    # Map to NLLB language codes
    src_code = NLLB_LANG_MAP.get(source_lang, 'eng_Latn')
    tgt_code = NLLB_LANG_MAP.get(target_lang, 'nld_Latn')
    
    model, tokenizer = get_nllb_model(model_name)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    
    # Tokenize
    tokenizer.src_lang = src_code
    inputs = tokenizer(text, return_tensors="pt", padding=True, truncation=True, max_length=512).to(device)
    
    # Get target language token ID
    forced_bos_token_id = tokenizer.convert_tokens_to_ids(tgt_code)
    
    # Translate with improved parameters
    translated_tokens = model.generate(
        **inputs,
        forced_bos_token_id=forced_bos_token_id,
        max_length=512,
        num_beams=8,  # Increased from 5 for better quality
        length_penalty=1.0,  # Encourage natural length
        early_stopping=True,
        no_repeat_ngram_size=3,  # Prevent repetition
        temperature=0.7  # More natural output
    )
    
    # Decode
    translation = tokenizer.batch_decode(translated_tokens, skip_special_tokens=True)[0]
    return translation


def refine_translation_with_llm(text: str, source_lang: str, target_lang: str, ollama_endpoint: str = "http://localhost:11434", ollama_model: str = "qwen2.5:7b", ollama_temperature: float = 0.3) -> str:
    """Refine machine translation using local LLM to make it more natural and idiomatic."""
    if not text or not text.strip():
        return text
    
    # Language name mapping for prompts
    lang_names = {
        'en': 'English',
        'nl': 'Dutch',
        'de': 'German',
        'fr': 'French',
        'es': 'Spanish',
        'it': 'Italian',
        'pt': 'Portuguese',
        'pl': 'Polish',
        'ru': 'Russian',
        'ja': 'Japanese',
        'ko': 'Korean',
        'zh': 'Chinese'
    }
    
    source_name = lang_names.get(source_lang, source_lang.upper())
    target_name = lang_names.get(target_lang, target_lang.upper())
    
    prompt = f"""You are a professional subtitle translator. Your task is to refine machine-translated subtitles to sound natural and idiomatic in {target_name}.

Original {source_name} context: This is from a movie/TV show subtitle.
Machine translation to {target_name}: {text}

Refine this translation to:
1. Sound natural and conversational in {target_name}
2. Use appropriate idioms and expressions
3. Maintain the same meaning and tone
4. Keep it concise (suitable for subtitles)
5. Preserve any names, numbers, or technical terms

Only output the refined {target_name} translation, nothing else."""

    try:
        # Use Ollama API
        client = ollama.Client(host=ollama_endpoint)
        response = client.generate(
            model=ollama_model,
            prompt=prompt,
            options={
                'temperature': ollama_temperature,
                'top_p': 0.9,
                'num_predict': 200  # Limit output length for subtitles
            }
        )
        
        refined = response['response'].strip()
        
        # Fallback to original if LLM output is suspiciously different in length
        if len(refined) > len(text) * 2 or len(refined) < len(text) * 0.3:
            print(f"⚠️ LLM output length suspicious, using original: '{text[:50]}...'")
            return text
        
        return refined
        
    except Exception as e:
        print(f"⚠️ LLM refinement failed: {e}, using original translation")
        return text


def translate_srt_content(srt_path: str, source_lang: str, target_lang: str, model_name="facebook/nllb-200-1.3B", clear_model=True, progress_callback=None, use_llm_refinement=False, ollama_endpoint="http://localhost:11434", ollama_model="qwen2.5:7b", ollama_temperature=0.3) -> list:
    """Translate SRT file content using NLLB with batching for better context."""
    method_desc = "NLLB → LLM refinement" if use_llm_refinement else "NLLB"
    print(f"Translating subtitles from {source_lang} to {target_lang} with {method_desc} (batch mode for better context)...")
    
    # Get model (will trigger download progress if needed)
    get_nllb_model(model_name, progress_callback=progress_callback)
    
    with open(srt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Parse SRT blocks
    blocks = content.strip().split('\n\n')
    translated_segments = []
    
    # Batch translate for better context (5 lines at a time)
    batch_size = 5
    for i in range(0, len(blocks), batch_size):
        batch = blocks[i:i + batch_size]
        batch_texts = []
        batch_meta = []
        
        for block in batch:
            lines = block.split('\n')
            if len(lines) >= 3:
                index = lines[0]
                timestamp = lines[1]
                text = '\n'.join(lines[2:])
                batch_texts.append(text)
                batch_meta.append({'index': index, 'timestamp': timestamp, 'original_text': text})
        
        if batch_texts:
            # Combine batch for context-aware translation
            combined_text = ' | '.join(batch_texts)  # Use separator for subtitle boundaries
            translated_combined = translate_text_nllb(combined_text, source_lang, target_lang, model_name)
            
            # Split back
            translated_parts = translated_combined.split(' | ')
            
            # If splitting didn't work perfectly, fall back to individual translation
            if len(translated_parts) != len(batch_texts):
                translated_parts = [translate_text_nllb(text, source_lang, target_lang, model_name) for text in batch_texts]
            
            # Apply LLM refinement if requested
            if use_llm_refinement:
                print(f"🤖 Refining translations with LLM ({ollama_model}, temp={ollama_temperature})...")
                translated_parts = [
                    refine_translation_with_llm(text, source_lang, target_lang, ollama_endpoint, ollama_model, ollama_temperature)
                    for text in translated_parts
                ]
            
            # Combine with metadata and adjust timestamps
            for meta, translated_text in zip(batch_meta, translated_parts):
                # Adjust timestamp based on text length ratio
                adjusted_timestamp = adjust_timestamp_for_length(
                    meta['timestamp'],
                    len(meta['original_text']),
                    len(translated_text)
                )
                
                translated_segments.append({
                    'index': meta['index'],
                    'timestamp': adjusted_timestamp,
                    'text': translated_text.strip()
                })
            
            print(f"Translated {len(translated_segments)}/{len(blocks)} segments...")
    
    # Clear NLLB model from GPU if requested
    if clear_model:
        clear_nllb_model()
    
    return translated_segments


def adjust_timestamp_for_length(timestamp: str, original_length: int, translated_length: int) -> str:
    """Adjust end timestamp based on text length ratio."""
    try:
        # Parse timestamp: 00:00:01,000 --> 00:00:03,000
        parts = timestamp.split(' --> ')
        if len(parts) != 2:
            return timestamp
        
        start_time = parts[0]
        end_time = parts[1]
        
        # Calculate length ratio (cap at 2.0x to avoid excessive extension)
        length_ratio = min(translated_length / max(original_length, 1), 2.0)
        
        # Only extend if translation is longer (ratio > 1.05)
        if length_ratio > 1.05:
            # Parse end time
            time_parts = end_time.split(':')
            if len(time_parts) == 3:
                hours = int(time_parts[0])
                minutes = int(time_parts[1])
                sec_ms = time_parts[2].split(',')
                seconds = int(sec_ms[0])
                milliseconds = int(sec_ms[1]) if len(sec_ms) == 2 else 0
                
                # Convert to total milliseconds
                total_ms = (hours * 3600 + minutes * 60 + seconds) * 1000 + milliseconds
                
                # Extend based on text length - more aggressive for longer translations
                extension_ms = int((length_ratio - 1) * 2000)  # Add up to 2 seconds for 2x length
                total_ms += extension_ms
                
                # Convert back
                hours = total_ms // 3600000
                total_ms %= 3600000
                minutes = total_ms // 60000
                total_ms %= 60000
                seconds = total_ms // 1000
                milliseconds = total_ms % 1000
                
                end_time = f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"
        
        return f"{start_time} --> {end_time}"
    except:
        return timestamp


def save_translated_srt(segments: list, output_path: str):
    """Save translated segments to SRT file."""
    with open(output_path, 'w', encoding='utf-8') as f:
        for seg in segments:
            f.write(f"{seg['index']}\n")
            f.write(f"{seg['timestamp']}\n")
            f.write(f"{seg['text']}\n\n")


def generate_srt_from_whisper(result: dict, output_path: str):
    """Generate SRT subtitle file from Whisper result with improved timing."""
    # Filter out non-speech annotations
    skip_patterns = ['[', '(MUSIC)', '(LAUGHTER)', '(APPLAUSE)', '(CHEERING)', 
                     '(SILENCE)', '(NOISE)', '(SOUND)', '(MUSIC PLAYING)', 
                     '(LAUGHING)', '(CLAPPING)', '(WHISTLING)']
    
    with open(output_path, 'w', encoding='utf-8') as f:
        subtitle_index = 1
        for segment in result['segments']:
            text = segment['text'].strip()
            
            # Skip empty segments
            if not text:
                continue
            
            # Skip segments that are just annotations
            text_upper = text.upper()
            if any(pattern in text_upper for pattern in skip_patterns):
                continue
            
            # Skip very short segments (likely noise)
            if len(text) < 3:
                continue
            
            # Use word-level timestamps if available for better accuracy
            if 'words' in segment and segment['words']:
                # Get first and last word timestamps
                start_time = segment['words'][0].get('start', segment.get('start', 0))
                end_time = segment['words'][-1].get('end', segment.get('end', 0))
            else:
                # Fallback to segment timestamps
                start_time = segment.get('start', 0)
                end_time = segment.get('end', 0)
            
            f.write(f"{subtitle_index}\n")
            f.write(f"{format_srt_time(start_time)} --> {format_srt_time(end_time)}\n")
            f.write(f"{text}\n\n")
            subtitle_index += 1



def main():
    parser = argparse.ArgumentParser(description='Transcribe MKV to SRT using Whisper AI')
    parser.add_argument('mkv_file', help='Path to MKV file')
    parser.add_argument('-l', '--language', help='Target language code (e.g., en, nl, de) for translation')
    parser.add_argument('--model', default='base', choices=['tiny', 'base', 'small', 'medium', 'large'], 
                        help='Whisper model size (default: base)')
    parser.add_argument('--keep-audio', action='store_true', help='Keep extracted audio file')
    parser.add_argument('--overwrite', action='store_true', help='Overwrite existing SRT files')
    parser.add_argument('--original-only', action='store_true', 
                        help='Only create original language SRT (skip EN and target translations)')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.mkv_file):
        print(f"Error: File not found: {args.mkv_file}")
        sys.exit(1)
    
    # Setup output paths
    mkv_path = Path(args.mkv_file)
    base_name = mkv_path.stem
    output_dir = mkv_path.parent
    
    # Check for existing files
    original_srt = output_dir / f"{base_name}.srt"
    en_srt = output_dir / f"{base_name}.en.srt"
    target_srt = output_dir / f"{base_name}.{args.language}.srt" if args.language else None
    
    if not args.overwrite:
        existing = []
        if original_srt.exists():
            existing.append(str(original_srt))
        if not args.original_only and en_srt.exists():
            existing.append(str(en_srt))
        if not args.original_only and target_srt and target_srt.exists():
            existing.append(str(target_srt))
        
        if existing:
            print(f"Error: Files already exist (use --overwrite to replace):")
            for f in existing:
                print(f"  - {f}")
            sys.exit(1)
    
    # Extract audio
    audio_path = extract_audio_from_mkv(str(mkv_path))
    
    try:
        # Step 1: Transcribe in original language
        print("\n=== Step 1: Original transcription ===")
        result = transcribe_audio_whisper(audio_path, language=None, model_size=args.model)
        detected_lang = result.get('language', 'unknown')
        print(f"✓ Detected language: {detected_lang}")
        
        generate_srt_from_whisper(result, str(original_srt))
        print(f"✓ Original SRT saved: {original_srt}")
        
        if args.original_only:
            print("\n✓ Original-only mode - skipping translations")
            return
        
        # Step 2: English translation (using Whisper)
        print("\n=== Step 2: English translation ===")
        if detected_lang == 'en':
            print("Already in English - copying original SRT")
            import shutil
            shutil.copy(str(original_srt), str(en_srt))
        else:
            en_result = translate_audio_whisper(audio_path, 'en', model_size=args.model)
            generate_srt_from_whisper(en_result, str(en_srt))
        print(f"✓ English SRT saved: {en_srt}")
        
        # Step 3: Target language translation (using NLLB)
        if args.language and args.language != detected_lang and args.language != 'en':
            print(f"\n=== Step 3: {args.language.upper()} translation (NLLB) ===")
            translated_segments = translate_srt_content(str(en_srt), 'en', args.language)
            save_translated_srt(translated_segments, str(target_srt))
            print(f"✓ {args.language.upper()} SRT saved: {target_srt}")
        elif args.language == detected_lang:
            print(f"\n=== Step 3: Target language same as original - copying ===")
            import shutil
            shutil.copy(str(original_srt), str(target_srt))
        
        print(f"\n✓ Complete! Generated {len(result['segments'])} subtitle segments")
        
    finally:
        if not args.keep_audio and os.path.exists(audio_path):
            os.remove(audio_path)


if __name__ == '__main__':
    main()
