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


def transcribe_audio_whisper(audio_path: str, language: str = None, model_size: str = "base") -> dict:
    """Transcribe audio file using OpenAI Whisper with GPU acceleration."""
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")
    if device == "cuda":
        print(f"GPU: {torch.cuda.get_device_name(0)}")
    
    print(f"Loading Whisper {model_size} model...")
    model = whisper.load_model(model_size, device=device)
    
    print(f"Transcribing {audio_path} with word-level timestamps...")
    result = model.transcribe(
        audio_path, 
        language=language, 
        verbose=True, 
        word_timestamps=True,  # Enable word-level timestamps for better accuracy
        condition_on_previous_text=True  # Better context for timing
    )
    
    return result


def translate_audio_whisper(audio_path: str, target_language: str, model_size: str = "base") -> dict:
    """Translate audio to target language using Whisper."""
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Translating to {target_language} with word-level timestamps...")
    
    model = whisper.load_model(model_size, device=device)
    
    # Whisper's task='translate' always translates to English
    result = model.transcribe(
        audio_path, 
        task='translate', 
        verbose=True, 
        word_timestamps=True,  # Enable word-level timestamps
        condition_on_previous_text=True  # Better timing accuracy
    )
    
    return result


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

def get_nllb_model():
    """Load NLLB model once and cache it."""
    global _nllb_model, _nllb_tokenizer
    
    if _nllb_model is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"Loading NLLB-200-1.3B translation model on {device}...")
        model_name = "facebook/nllb-200-1.3B"
        
        _nllb_tokenizer = AutoTokenizer.from_pretrained(model_name)
        _nllb_model = AutoModelForSeq2SeqLM.from_pretrained(model_name).to(device)
        print("✓ NLLB-1.3B model loaded")
    
    return _nllb_model, _nllb_tokenizer


def translate_text_nllb(text: str, source_lang: str, target_lang: str) -> str:
    """Translate text using NLLB-200 model."""
    if not text or not text.strip():
        return text
    
    # Map to NLLB language codes
    src_code = NLLB_LANG_MAP.get(source_lang, 'eng_Latn')
    tgt_code = NLLB_LANG_MAP.get(target_lang, 'nld_Latn')
    
    model, tokenizer = get_nllb_model()
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


def translate_srt_content(srt_path: str, source_lang: str, target_lang: str) -> list:
    """Translate SRT file content using NLLB with batching for better context."""
    print(f"Translating subtitles from {source_lang} to {target_lang} (batch mode for better context)...")
    
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
            translated_combined = translate_text_nllb(combined_text, source_lang, target_lang)
            
            # Split back
            translated_parts = translated_combined.split(' | ')
            
            # If splitting didn't work perfectly, fall back to individual translation
            if len(translated_parts) != len(batch_texts):
                translated_parts = [translate_text_nllb(text, source_lang, target_lang) for text in batch_texts]
            
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
