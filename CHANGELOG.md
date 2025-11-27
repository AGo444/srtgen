# Changelog

All notable changes to SRTGEN will be documented in this file.

## [1.3.0] - 2025-11-27

### ✨ Added
- **NLLB+LLM Integration**: New translation method using Ollama for natural, conversational subtitles
  - Combines NLLB speed with LLM refinement
  - Configurable endpoint, model, and temperature (0.0-2.0)
  - Default: qwen2.5:7b at temperature 0.3
  - Prompt engineering for subtitle context and natural phrasing
  - Length validation with automatic fallback
- **Ollama Configuration UI**: Full settings interface for LLM integration
  - Endpoint configuration (default: http://localhost:11434)
  - Model selection (any Ollama-compatible model)
  - Temperature control slider with validation
  - Settings persist to /output/config.json
- **Translation Method in History**: Job history now displays which method was used
  - Shows Whisper, NLLB, NLLB+Whisper, or NLLB+LLM
  - Fallback to "Whisper" for legacy entries without method field
- **Enhanced Settings UI**: Complete visual refresh
  - Modern form inputs with 2px borders and 6px radius
  - Hover and focus states with primary color highlights
  - Gradient buttons with lift animations
  - Bold, uppercase column headers with letter-spacing
  - Enhanced help text boxes with better padding
  - Consistent 3-column grid layout with 40px gaps
  - Improved modal styling with animations

### 🔧 Improved
- **GPU Memory Management**: Aggressive cleanup optimizations
  - 5x gc.collect() passes (increased from 3x)
  - torch.cuda.reset_peak_memory_stats() calls
  - torch.cuda.reset_accumulated_memory_stats() calls
  - Explicit cleanup between NLLB and Whisper loads
  - PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True in Dockerfile
- **NLLB+LLM Method Recognition**: Fixed translation method validation
  - Added 'nllb-llm' to skip_initial_transcription check
  - Added 'nllb-llm' to NLLB fallback validation
  - LLM refinement now properly triggers in all code paths
- **Settings Persistence**: All 10 settings now save/load correctly
  - Including ollamaEndpoint, ollamaModel, ollamaTemperature
  - Fixed localStorage handling for new Ollama settings
  - Server-side validation for temperature range (0.0-2.0)
- **Filter Persistence**: SRT filter selection now persists across directory changes
  - Saves to localStorage as 'srtFilter'
  - Restores on loadFiles() calls
  - Maintains user preference during navigation

### 🔒 Security
- **CVE-2025-65018 Fixed**: Updated libpng1.6 (1.6.48-1+) via apt-get upgrade
- **Pip Vulnerability Fixed**: Upgraded pip to 25.3+ to patch security issues
- **Dockerfile Hardening**: Added apt-get upgrade step to patch system packages

### 🐛 Fixed
- **Docker Compose Disabled**: Moved to Unraid-managed container
  - docker-compose.yml renamed to .backup to prevent accidental usage
  - Created update-srtgen.sh script for manual updates
  - Container now fully managed via Unraid Docker UI
- **Unraid Template Icon**: Embedded base64 data URI for persistent icon display
  - No longer requires external GitHub hosting
  - ~2.2KB inline icon in template XML
  - Always visible in Unraid interface
- **WebUI Button Missing**: Fixed Unraid WebUI link
  - Removed trailing slash from WebUI URL
  - Container now properly shows WebUI button in Unraid

### 📝 Documentation
- Updated help modal with NLLB+LLM method description
- Added Ollama configuration instructions
- Enhanced translation method explanations
- Updated temperature control documentation

### ⚙️ Configuration
- New config keys: ollamaEndpoint, ollamaModel, ollamaTemperature
- Backward compatible with existing config.json files
- All settings now persist across container restarts

### 🏗️ Infrastructure
- Docker resource limits: 8 CPU cores, 16GB RAM
- Update script for easy rebuilds: /mnt/user/appdata/SRTGEN/update-srtgen.sh
- Unraid template updated with embedded icon

---

## [1.2.0] - 2025-11-20

### ✨ Added
- **Bandwidth Rate Limiting**: Configurable download speed control
  - Percentage-based slider (10%-100%)
  - Automatic detection of available bandwidth
  - Server-side enforcement via MODEL_DOWNLOAD_SPEED_MB
- **3-Column Settings Layout**: Reorganized settings modal for better UX
  - Model Settings | Performance | Advanced Options
  - Column headers with visual hierarchy
  - Improved spacing and readability
- **Filter Dropdown Styling**: Enhanced SRT filter appearance
  - Matches search box design consistency
  - 2px borders, hover states, smooth transitions
- **HuggingFace Tokenizer Fix**: Resolved chat_template dict error
  - Manual tokenizer_config.json modification
  - Workaround for NLLB model compatibility

### 🔧 Improved
- **UI Performance**: DocumentFragment batch rendering for large file lists
  - BATCH_SIZE=50 for smooth scrolling
  - Reduced DOM manipulation overhead
- **Config Persistence**: All 10 settings now save to /output/config.json
  - Frontend and backend settings unified
  - Survives container restarts
- **NLLB+Whisper Optimization**: Smart English SRT reuse
  - Skips redundant transcription when .en.srt exists
  - Faster processing for multi-language workflows

### 🐛 Fixed
- Translation method persistence issue
- Filter dropdown not respecting saved selection
- Config loading race conditions

---

## [1.1.0] - 2025-11-15

### ✨ Added
- **Job History**: Complete transcription history with filtering
  - Filter by: All / Completed / Failed / Cancelled
  - Delete individual or filtered entries
  - Duration tracking and error details
- **Queue Management**: 
  - Bump pending jobs to first position (🔼 button)
  - Clear all pending jobs (🗑️ button)
  - Cancel running jobs (❌ button)
- **Status Indicators**: Visual job status in file browser
  - ✓ Completed (green)
  - ⟳ Running (blue pulse)
  - ⧗ Pending (orange)
  - ✗ Failed (red)

### 🔧 Improved
- File browser now remembers last visited path
- Search functionality with debouncing
- Better error messages for failed jobs

---

## [1.0.0] - 2025-11-01

### 🎉 Initial Release
- OpenAI Whisper transcription (tiny/base/small/medium/large)
- Facebook NLLB-200-1.3B translation (200+ languages)
- Triple SRT output (original + English + target)
- Web UI with file browser and job queue
- Docker container with NVIDIA GPU support
- Unraid Community Applications template
- Bazarr post-processing integration
- CLI interface for automation
- Settings persistence (localStorage)
- Background job processing with threading
- FFmpeg audio extraction
- Word-level timestamp synchronization

---

## Version Numbering

- **Major (X.0.0)**: Breaking changes, major feature additions
- **Minor (1.X.0)**: New features, UI changes, significant improvements
- **Patch (1.0.X)**: Bug fixes, minor tweaks, documentation updates
