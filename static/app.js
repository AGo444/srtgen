let currentPath = '';
let selectedFile = null;
let jobs = {};
let allFiles = []; // Store all files for filtering
let isFolderMode = false; // Track folder selection mode
let selectedFolderFiles = []; // Files selected for batch processing
let excludedFiles = new Set(); // Files excluded from batch processing
let hasSubfolders = false; // Whether current folder has subfolders
let displayedFiles = []; // Files currently rendered
const BATCH_SIZE = 50; // Number of items to render at once
let renderIndex = 0; // Current rendering position
let isProcessing = false; // Prevent double submissions
let detectedBandwidthMbps = null; // Detected network speed

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    console.log('🚀 SRTGEN initialized - Lazy Loading v2.1');
    loadSettings();
    
    // Restore last path from localStorage or load root
    const lastPath = localStorage.getItem('currentPath') || '';
    console.log('📂 Loading path:', lastPath);
    loadFiles(lastPath);
    
    setupEventListeners();
    startJobPolling();
    detectBandwidth();
});

function setupEventListeners() {
    document.getElementById('transcribeBtn').addEventListener('click', startTranscription);
    document.getElementById('selectFolderBtn').addEventListener('click', selectCurrentFolder);
    document.getElementById('saveSettingsBtn').addEventListener('click', saveSettings);
    document.getElementById('settingsToggle').addEventListener('click', openSettings);
    document.getElementById('closeSettings').addEventListener('click', closeSettings);
    document.getElementById('helpToggle').addEventListener('click', openHelp);
    document.getElementById('closeHelp').addEventListener('click', closeHelp);
    document.getElementById('historyToggle').addEventListener('click', openHistory);
    document.getElementById('clearQueueBtn').addEventListener('click', clearQueue);
    
    // SRT Filter dropdown
    document.getElementById('srtFilter').addEventListener('change', filterBySRT);
    document.getElementById('addFilteredBtn').addEventListener('click', addFilteredToQueue);
    
    // Update button text when translation method changes
    const translationMethodSelect = document.getElementById('translationMethod');
    if (translationMethodSelect) {
        translationMethodSelect.addEventListener('change', updateTranscribeButtonText);
    }
    
    // Download speed slider
    const downloadSpeedSlider = document.getElementById('downloadSpeedLimit');
    const downloadSpeedValue = document.getElementById('downloadSpeedValue');
    if (downloadSpeedSlider && downloadSpeedValue) {
        downloadSpeedSlider.addEventListener('input', (e) => {
            const percent = parseInt(e.target.value);
            updateDownloadSpeedDisplay(percent);
        });
    }
    
    // Close modals when clicking outside
    window.addEventListener('click', (e) => {
        const settingsModal = document.getElementById('settingsModal');
        const historyModal = document.getElementById('historyModal');
        const helpModal = document.getElementById('helpModal');
        if (e.target === settingsModal) {
            closeSettings();
        }
        if (e.target === historyModal) {
            closeHistory();
        }
        if (e.target === helpModal) {
            closeHelp();
        }
    });
    
    // Breadcrumb navigation
    document.querySelector('.breadcrumb-item').addEventListener('click', () => {
        loadFiles('');
    });
    
    // Search functionality
    const searchInput = document.getElementById('searchInput');
    searchInput.addEventListener('input', (e) => {
        filterFiles(e.target.value);
    });
    
    // Rescan folder when subfolder checkbox changes
    document.getElementById('includeSubfolders').addEventListener('change', () => {
        if (isFolderMode) {
            selectCurrentFolder();
        }
    });
}

// Helper function to get translation method label
function getTranslationMethodLabel(method) {
    // Allow passing method as parameter or reading from localStorage
    if (!method) {
        method = localStorage.getItem('translationMethod') || 'whisper';
    }
    
    switch(method) {
        case 'whisper':
            return 'Whisper';
        case 'nllb':
            return 'NLLB';
        case 'nllb-whisper':
            return 'NLLB+Whisper';
        case 'nllb-llm':
            return 'NLLB+LLM';
        default:
            return 'Whisper';
    }
}

// Update transcribe button text based on file type and translation method
function updateTranscribeButtonText() {
    const btn = document.getElementById('transcribeBtn');
    const enableSrtTranslation = localStorage.getItem('enableSrtTranslation') === 'true';
    const isSrtFile = selectedFile && selectedFile.name.toLowerCase().endsWith('.srt');
    const methodLabel = getTranslationMethodLabel();
    
    if (isSrtFile && enableSrtTranslation) {
        btn.textContent = `🌍 Translate SRT File (${methodLabel})`;
    } else {
        btn.textContent = `Generate Subtitles (${methodLabel})`;
    }
}

function openSettings() {
    document.getElementById('settingsModal').style.display = 'flex';
    
    // Sync default language to transcription language (only add listener once)
    const defaultLangElement = document.getElementById('defaultLanguage');
    if (defaultLangElement && !defaultLangElement.dataset.listenerAttached) {
        defaultLangElement.addEventListener('change', (e) => {
            const langElement = document.getElementById('language');
            if (langElement) {
                langElement.value = e.target.value;
            }
        });
        defaultLangElement.dataset.listenerAttached = 'true';
    }
}

function closeSettings() {
    document.getElementById('settingsModal').style.display = 'none';
}

async function detectBandwidth() {
    const detectedSpeedEl = document.getElementById('detectedSpeed');
    if (!detectedSpeedEl) return;
    
    detectedSpeedEl.textContent = 'measuring...';
    
    try {
        // Download a small file to measure speed
        const startTime = performance.now();
        const response = await fetch('/static/style.css?t=' + Date.now(), { cache: 'no-store' });
        const blob = await response.blob();
        const endTime = performance.now();
        
        const bytes = blob.size;
        const durationSeconds = (endTime - startTime) / 1000;
        const mbps = (bytes * 8 / 1000000) / durationSeconds;
        
        detectedBandwidthMbps = Math.max(mbps, 10); // Minimum 10 Mbps
        
        detectedSpeedEl.textContent = `${detectedBandwidthMbps.toFixed(1)} Mbps`;
        console.log(`📊 Detected bandwidth: ${detectedBandwidthMbps.toFixed(1)} Mbps`);
        
        // Update display with current slider value now that we have bandwidth data
        const slider = document.getElementById('downloadSpeedLimit');
        if (slider) {
            updateDownloadSpeedDisplay(parseInt(slider.value));
        }
    } catch (err) {
        console.warn('⚠️ Could not detect bandwidth:', err);
        detectedBandwidthMbps = 100; // Assume 100 Mbps
        detectedSpeedEl.textContent = '~100 Mbps (assumed)';
        
        // Update display even with assumed bandwidth
        const slider = document.getElementById('downloadSpeedLimit');
        if (slider) {
            updateDownloadSpeedDisplay(parseInt(slider.value));
        }
    }
}

function updateDownloadSpeedDisplay(percent) {
    const valueEl = document.getElementById('downloadSpeedValue');
    if (!valueEl) return;
    
    if (percent === 100) {
        valueEl.textContent = '100% (no limit)';
        return;
    }
    
    // Calculate actual speed: higher percentage = higher speed (intuitive)
    // 0% = 1 MB/s minimum, 100% = full speed
    let speedMBps;
    
    if (detectedBandwidthMbps) {
        // Use detected bandwidth
        const maxSpeedMBps = (detectedBandwidthMbps / 8); // Convert Mbps to MB/s
        speedMBps = Math.max(1, maxSpeedMBps * (percent / 100));
    } else {
        // Fallback calculation
        speedMBps = Math.max(1, 10 * (percent / 100));
    }
    
    valueEl.textContent = `${percent}% (~${speedMBps.toFixed(1)} MB/s)`;
}

function openHelp() {
    document.getElementById('helpModal').style.display = 'flex';
}

function closeHelp() {
    document.getElementById('helpModal').style.display = 'none';
}

async function loadSettings() {
    // Load from server
    try {
        const response = await fetch('/api/settings');
        if (response.ok) {
            const config = await response.json();
            console.log('⚙️ Loaded config from server:', config);
            
            // Backend settings
            if (config.max_concurrent_jobs !== undefined) {
                localStorage.setItem('maxConcurrentJobs', config.max_concurrent_jobs);
            }
            if (config.chunk_length !== undefined) {
                localStorage.setItem('chunkLength', config.chunk_length);
            }
            if (config.translation_method !== undefined) {
                localStorage.setItem('translationMethod', config.translation_method);
            }
            if (config.ollamaEndpoint !== undefined) {
                localStorage.setItem('ollamaEndpoint', config.ollamaEndpoint);
            }
            if (config.ollamaModel !== undefined) {
                localStorage.setItem('ollamaModel', config.ollamaModel);
            }
            if (config.ollamaTemperature !== undefined) {
                localStorage.setItem('ollamaTemperature', config.ollamaTemperature);
            }
            
            // Frontend settings
            if (config.defaultLanguage !== undefined) {
                localStorage.setItem('defaultLanguage', config.defaultLanguage);
            }
            if (config.whisperModel !== undefined) {
                localStorage.setItem('whisperModel', config.whisperModel);
            }
            if (config.translationModel !== undefined) {
                localStorage.setItem('translationModel', config.translationModel);
            }
            if (config.overwriteExisting !== undefined) {
                localStorage.setItem('enableSrtTranslation', config.overwriteExisting);
            }
            if (config.downloadSpeedLimit !== undefined) {
                localStorage.setItem('downloadSpeedLimit', config.downloadSpeedLimit);
            } else {
                // Default to 50% if not set
                localStorage.setItem('downloadSpeedLimit', '50');
            }
        }
    } catch (err) {
        console.warn('⚠️ Could not load config from server, using localStorage:', err);
    }
    
    // Apply to UI
    const defaultLang = localStorage.getItem('defaultLanguage') || 'nl-NL';
    const whisperModel = localStorage.getItem('whisperModel') || 'medium';
    const translationModel = localStorage.getItem('translationModel') || 'nllb-200-1.3B';
    const maxConcurrentJobs = localStorage.getItem('maxConcurrentJobs') || '2';
    const chunkLength = localStorage.getItem('chunkLength') || '30';
    const translationMethod = localStorage.getItem('translationMethod') || 'whisper';
    const enableSrtTranslation = localStorage.getItem('enableSrtTranslation') === 'true';
    const downloadSpeedLimit = localStorage.getItem('downloadSpeedLimit') || '50';
    const ollamaEndpoint = localStorage.getItem('ollamaEndpoint') || 'http://localhost:11434';
    const ollamaModel = localStorage.getItem('ollamaModel') || 'qwen2.5:7b';
    const ollamaTemperature = localStorage.getItem('ollamaTemperature') || '0.3';
    
    // Safely set values if elements exist
    const defaultLangEl = document.getElementById('defaultLanguage');
    const whisperModelEl = document.getElementById('whisperModel');
    const translationModelEl = document.getElementById('translationModel');
    const maxConcurrentJobsEl = document.getElementById('maxConcurrentJobs');
    const chunkLengthEl = document.getElementById('chunkLength');
    const translationMethodEl = document.getElementById('translationMethod');
    const enableSrtTranslationEl = document.getElementById('enableSrtTranslation');
    const languageEl = document.getElementById('language');
    const downloadSpeedLimitEl = document.getElementById('downloadSpeedLimit');
    const ollamaEndpointEl = document.getElementById('ollamaEndpoint');
    const ollamaModelEl = document.getElementById('ollamaModel');
    const ollamaTemperatureEl = document.getElementById('ollamaTemperature');
    
    if (defaultLangEl) defaultLangEl.value = defaultLang;
    if (whisperModelEl) whisperModelEl.value = whisperModel;
    if (translationModelEl) translationModelEl.value = translationModel;
    if (maxConcurrentJobsEl) maxConcurrentJobsEl.value = maxConcurrentJobs;
    if (chunkLengthEl) chunkLengthEl.value = chunkLength;
    if (translationMethodEl) translationMethodEl.value = translationMethod;
    if (enableSrtTranslationEl) enableSrtTranslationEl.checked = enableSrtTranslation;
    if (languageEl) languageEl.value = defaultLang;
    if (downloadSpeedLimitEl) {
        downloadSpeedLimitEl.value = downloadSpeedLimit;
        updateDownloadSpeedDisplay(parseInt(downloadSpeedLimit));
    }
    if (ollamaEndpointEl) ollamaEndpointEl.value = ollamaEndpoint;
    if (ollamaModelEl) ollamaModelEl.value = ollamaModel;
    if (ollamaTemperatureEl) ollamaTemperatureEl.value = ollamaTemperature;
    
    console.log('⚙️ Settings applied to UI:', { defaultLang, whisperModel, translationModel, maxConcurrentJobs, chunkLength, translationMethod, enableSrtTranslation, downloadSpeedLimit, ollamaEndpoint, ollamaModel, ollamaTemperature });
}

async function saveSettings() {
    const defaultLang = document.getElementById('defaultLanguage').value;
    const whisperModel = document.getElementById('whisperModel').value;
    const translationModel = document.getElementById('translationModel').value;
    const maxConcurrentJobs = document.getElementById('maxConcurrentJobs').value;
    const chunkLength = document.getElementById('chunkLength').value;
    const translationMethod = document.getElementById('translationMethod').value;
    const enableSrtTranslation = document.getElementById('enableSrtTranslation').checked;
    const downloadSpeedLimit = document.getElementById('downloadSpeedLimit').value;
    const ollamaEndpoint = document.getElementById('ollamaEndpoint').value;
    const ollamaModel = document.getElementById('ollamaModel').value;
    const ollamaTemperature = document.getElementById('ollamaTemperature').value;
    
    localStorage.setItem('defaultLanguage', defaultLang);
    localStorage.setItem('whisperModel', whisperModel);
    localStorage.setItem('translationModel', translationModel);
    localStorage.setItem('maxConcurrentJobs', maxConcurrentJobs);
    localStorage.setItem('chunkLength', chunkLength);
    localStorage.setItem('translationMethod', translationMethod);
    localStorage.setItem('enableSrtTranslation', enableSrtTranslation);
    localStorage.setItem('downloadSpeedLimit', downloadSpeedLimit);
    localStorage.setItem('ollamaEndpoint', ollamaEndpoint);
    localStorage.setItem('ollamaModel', ollamaModel);
    localStorage.setItem('ollamaTemperature', ollamaTemperature);
    document.getElementById('language').value = defaultLang;
    
    // Calculate actual MB/s from percentage
    let downloadSpeedMBps = 5; // Default fallback
    if (detectedBandwidthMbps) {
        const percent = parseInt(downloadSpeedLimit);
        if (percent === 100) {
            downloadSpeedMBps = 0; // No limit
        } else {
            const maxSpeedMBps = (detectedBandwidthMbps / 8);
            downloadSpeedMBps = Math.max(1, Math.round(maxSpeedMBps * (percent / 100)));
        }
    }
    
    // Send ALL settings to server for persistence
    try {
        const response = await fetch('/api/settings', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ 
                max_concurrent_jobs: parseInt(maxConcurrentJobs),
                chunk_length: parseInt(chunkLength),
                translation_method: translationMethod,
                defaultLanguage: defaultLang,
                whisperModel: whisperModel,
                translationModel: translationModel,
                overwriteExisting: enableSrtTranslation,
                downloadSpeedLimit: parseInt(downloadSpeedLimit),
                downloadSpeedMBps: downloadSpeedMBps,
                ollamaEndpoint: ollamaEndpoint,
                ollamaModel: ollamaModel,
                ollamaTemperature: parseFloat(ollamaTemperature)
            })
        });
        
        if (!response.ok) {
            console.error('Failed to save settings to server');
        } else {
            console.log('✓ Settings saved to server');
        }
    } catch (err) {
        console.error('Failed to update server settings:', err);
    }
    
    // Visual feedback
    const btn = document.getElementById('saveSettingsBtn');
    const originalText = btn.textContent;
    btn.textContent = '✓ Saved!';
    setTimeout(() => {
        btn.textContent = originalText;
        closeSettings();
    }, 1000);
}

async function addSelectedToQueue() {
    if (!isFolderMode || selectedFolderFiles.length === 0) return;
    
    // Prevent double submission
    if (isProcessing) {
        console.log('⚠️ Already processing, ignoring duplicate click');
        return;
    }
    isProcessing = true;
    
    const language = document.getElementById('language').value;
    const overwrite = document.getElementById('overwrite').checked;
    const whisperModel = localStorage.getItem('whisperModel') || 'medium';
    const translationModel = localStorage.getItem('translationModel') || 'nllb-200-1.3B';
    const translationMethod = localStorage.getItem('translationMethod') || 'whisper';
    const chunkLength = localStorage.getItem('chunkLength') || '30';
    
    // Filter out excluded files
    const filesToProcess = selectedFolderFiles
        .filter(f => !excludedFiles.has(f.path))
        .map(f => f.path);
    
    if (filesToProcess.length === 0) {
        alert('No files selected for processing');
        isProcessing = false;
        return;
    }
    
    // Check for duplicates - filter out files already in queue
    const existingFiles = Object.values(jobs)
        .filter(j => j.status === 'pending' || j.status === 'running')
        .map(j => j.file);
    
    const newFiles = [];
    const duplicates = [];
    
    filesToProcess.forEach(file => {
        const fileStr = typeof file === 'string' ? file : (file.path || file.name || String(file));
        const fileName = fileStr.split('/').pop();
        
        // Check if this file (or its filename) is already in queue
        const isDuplicate = existingFiles.some(existing => {
            const existingStr = typeof existing === 'string' ? existing : (existing.path || existing.name || String(existing));
            const existingFileName = existingStr.split('/').pop();
            // Match by full path or by filename
            return existing === file || existingFileName === fileName;
        });
        
        if (isDuplicate) {
            duplicates.push(fileName);
        } else {
            newFiles.push(file);
        }
    });
    
    const skippedCount = duplicates.length;
    
    if (skippedCount > 0) {
        console.log('⚠️ Skipped duplicates:', duplicates);
    }
    
    if (newFiles.length === 0) {
        alert(`All ${filesToProcess.length} file(s) are already in the queue`);
        isProcessing = false;
        return;
    }
    
    try {
        const response = await fetch('/api/transcribe/batch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                files: newFiles,
                language: language,
                overwrite: overwrite,
                whisper_model: whisperModel,
                translation_model: translationModel,
                translation_method: translationMethod,
                chunk_length: parseInt(chunkLength)
            })
        });
        
        const data = await response.json();
        
        if (data.error) {
            alert('Error: ' + data.error);
        } else {
            console.log('📋 Adding jobs to queue:', data.jobs);
            
            // Add all jobs to the queue
            data.jobs.forEach(job => {
                jobs[job.job_id] = {
                    id: job.job_id,
                    file: job.file,
                    status: 'pending'
                };
                console.log('  ➕ Job added:', job.job_id, '→', job.file);
            });
            
            console.log('📊 Total jobs now:', Object.keys(jobs).length);
            
            const message = skippedCount > 0 
                ? `✓ Added ${data.jobs.length} file(s) to the queue (${skippedCount} skipped - already in queue)`
                : `✓ Added ${data.jobs.length} file(s) to the queue!`;
            
            alert(message);
            updateJobList();
            
            // Don't clear folder selection - just hide the selected files list
            document.getElementById('selectedFilesList').style.display = 'none';
            document.getElementById('subfolderLabel').style.display = 'none';
            isFolderMode = false;
            
            // Refresh status indicators after adding to queue
            setTimeout(() => refreshFileStatusIndicators(), 100);
        }
    } catch (error) {
        alert('Error: ' + error.message);
    } finally {
        isProcessing = false;
    }
}

async function loadFiles(path) {
    currentPath = path;
    
    // Save current path to localStorage
    localStorage.setItem('currentPath', path);
    
    const fileList = document.getElementById('fileList');
    const searchInput = document.getElementById('searchInput');
    const srtFilterSelect = document.getElementById('srtFilter');
    
    fileList.innerHTML = '<div class="loading">Loading files...</div>';
    searchInput.value = ''; // Clear search when navigating
    
    // Restore saved filter setting
    const savedFilter = localStorage.getItem('srtFilter') || 'all';
    if (srtFilterSelect) {
        srtFilterSelect.value = savedFilter;
    }
    
    console.log('📡 Fetching:', `/api/browse?path=${path}`);
    
    try {
        const response = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
        const data = await response.json();
        
        console.log('📦 Received data:', { itemCount: data.items?.length, path: data.current_path });
        
        // Show message for large folders
        if (data.items && data.items.length > 100) {
            fileList.innerHTML = `<div class="loading">Loading ${data.items.length} items... Rendering in batches...</div>`;
            console.log(`⚡ Large folder detected: ${data.items.length} items`);
        }
        
        if (data.error) {
            console.error('❌ API Error:', data.error);
            fileList.innerHTML = `<div class="loading">Error: ${data.error}</div>`;
            return;
        }
        
        allFiles = data.items; // Store for filtering
        console.log('✅ Rendering', allFiles.length, 'items');
        
        // Apply saved filter if not 'all'
        if (savedFilter !== 'all') {
            await filterBySRT();
        } else {
            renderFiles(allFiles);
        }
        
        updateBreadcrumb(path);
        
        // Check if folder has subfolders and video files
        hasSubfolders = data.items.some(item => item.type === 'directory');
        const hasVideos = data.items.some(item => item.type === 'file');
        
        // Enable folder button if we're in a directory (path is not empty)
        // It should work for folders with files, subfolders, or both
        const folderBtn = document.getElementById('selectFolderBtn');
        folderBtn.disabled = !path;
    } catch (error) {
        fileList.innerHTML = `<div class="loading">Error loading files: ${error.message}</div>`;
    }
}

function filterFiles(searchTerm) {
    if (!searchTerm.trim()) {
        renderFiles(allFiles);
        return;
    }
    
    const filtered = allFiles.filter(item => 
        item.name.toLowerCase().includes(searchTerm.toLowerCase())
    );
    
    renderFiles(filtered);
}

function renderFiles(items) {
    const fileList = document.getElementById('fileList');
    
    console.log('🎨 renderFiles called with', items.length, 'items');
    
    if (items.length === 0) {
        fileList.innerHTML = '<div class="loading">No files found</div>';
        return;
    }
    
    // Clear existing content
    fileList.innerHTML = '';
    displayedFiles = items;
    renderIndex = 0;
    
    console.log('🔄 Starting batch rendering...');
    // Initial render
    renderBatch();
    
    // Setup infinite scroll
    fileList.addEventListener('scroll', handleScroll);
}

function handleScroll() {
    const fileList = document.getElementById('fileList');
    const scrollPosition = fileList.scrollTop + fileList.clientHeight;
    const scrollHeight = fileList.scrollHeight;
    
    // Load more when 80% scrolled
    if (scrollPosition >= scrollHeight * 0.8 && renderIndex < displayedFiles.length) {
        renderBatch();
    }
}

function renderBatch() {
    const fileList = document.getElementById('fileList');
    const endIndex = Math.min(renderIndex + BATCH_SIZE, displayedFiles.length);
    
    console.log(`📊 Rendering batch ${renderIndex}-${endIndex} of ${displayedFiles.length}`);
    
    // Remove old loading indicator if it exists
    const oldLoader = fileList.querySelector('.loading-more');
    if (oldLoader) oldLoader.remove();
    
    const fragment = document.createDocumentFragment();
    
    for (let i = renderIndex; i < endIndex; i++) {
        const item = displayedFiles[i];
        const div = document.createElement('div');
        div.className = 'file-item';
        div.dataset.path = item.path;
        div.dataset.type = item.type;
        
        // Check job status for this file
        if (item.type === 'file') {
            const fileName = item.name;
            const fileJob = Object.values(jobs).find(j => 
                j.file === item.path || j.file === fileName || j.file.endsWith(fileName)
            );
            
            if (fileJob) {
                if (fileJob.status === 'pending') div.classList.add('status-queued');
                else if (fileJob.status === 'running') div.classList.add('status-processing');
                else if (fileJob.status === 'completed') div.classList.add('status-success');
                else if (fileJob.status === 'failed') div.classList.add('status-error');
            }
        }
        
        if (item.type === 'directory') {
            div.innerHTML = `
                <div class="file-info">
                    <div class="file-name">📁 ${item.name}</div>
                </div>
            `;
            div.addEventListener('click', () => loadFiles(item.path));
        } else {
            const srtStatus = item.srt.exists 
                ? `<span class="srt-status exists">✓ SRT ${(item.srt.languages || []).map(lang => `<span class="srt-language">${lang}</span>`).join(' ')}</span>`
                : `<span class="srt-status missing">⚠ No SRT</span>`;
            
            // Check if file is in queue
            const fileJob = Object.values(jobs).find(j => j.file === item.path && (j.status === 'pending' || j.status === 'running'));
            const inQueue = !!fileJob;
            const queueButtonText = inQueue ? '✓ In Queue' : '+ Queue';
            const queueButtonClass = inQueue ? 'btn-add-to-queue in-queue' : 'btn-add-to-queue';
            
            div.innerHTML = `
                <div class="file-info">
                    <div class="file-name">🎬 ${item.name}</div>
                    <div class="file-meta">
                        <span class="file-size">${formatFileSize(item.size)}</span>
                        ${srtStatus}
                    </div>
                </div>
                <div class="file-actions">
                    <button class="${queueButtonClass}" onclick="event.stopPropagation(); addFileToQueue('${item.path.replace(/'/g, "\\'")}')"
                        ${inQueue ? 'disabled' : ''}>${queueButtonText}</button>
                </div>
            `;
            div.addEventListener('click', () => selectFile(item));
        }
        
        fragment.appendChild(div);
    }
    
    fileList.appendChild(fragment);
    renderIndex = endIndex;
    
    console.log(`✅ Batch rendered. Total now: ${fileList.children.length} elements`);
    
    // Show loading indicator if more items to load
    if (renderIndex < displayedFiles.length) {
        const loadingDiv = document.createElement('div');
        loadingDiv.className = 'loading-more';
        loadingDiv.textContent = `Showing ${renderIndex} of ${displayedFiles.length} items...`;
        fileList.appendChild(loadingDiv);
    }
}

function selectFile(file) {
    selectedFile = file;
    isFolderMode = false;
    selectedFolderFiles = [];
    excludedFiles.clear();
    
    // Hide folder selection UI
    document.getElementById('selectedFilesList').style.display = 'none';
    document.getElementById('subfolderLabel').style.display = 'none';
    
    // Update UI - add selected class
    document.querySelectorAll('.file-item').forEach(item => {
        item.classList.remove('selected');
    });
    
    const selectedElement = document.querySelector(`[data-path="${file.path}"]`);
    if (selectedElement) {
        selectedElement.classList.add('selected');
    }
    
    // Check if SRT translation mode is enabled
    const enableSrtTranslation = localStorage.getItem('enableSrtTranslation') === 'true';
    const isSrtFile = file.name.toLowerCase().endsWith('.srt');
    
    // Enable transcribe button based on file type and settings
    if (isSrtFile && enableSrtTranslation) {
        // In SRT translation mode with SRT file selected
        document.getElementById('transcribeBtn').disabled = false;
        updateTranscribeButtonText();
    } else if (!isSrtFile) {
        // Video file selected
        document.getElementById('transcribeBtn').disabled = false;
        updateTranscribeButtonText();
    } else {
        // SRT file but translation mode disabled
        document.getElementById('transcribeBtn').disabled = true;
        updateTranscribeButtonText();
    }
}

async function selectCurrentFolder() {
    if (!currentPath) return;
    
    isFolderMode = true;
    selectedFile = null;
    excludedFiles.clear();
    
    // Always show subfolder checkbox when button is clicked, auto-check if no direct videos
    const subfolderLabel = document.getElementById('subfolderLabel');
    const subfolderCheckbox = document.getElementById('includeSubfolders');
    const hasDirectVideos = allFiles.some(f => f.type === 'file');
    
    // If no direct video files, automatically enable recursive and show checkbox
    if (!hasDirectVideos && hasSubfolders) {
        subfolderCheckbox.checked = true;
    }
    
    subfolderLabel.style.display = hasSubfolders ? 'flex' : 'none';
    
    // Clear file selection in file browser
    document.querySelectorAll('.file-item').forEach(item => {
        item.classList.remove('selected');
    });
    
    // Scan for video files
    const includeSubfolders = document.getElementById('includeSubfolders').checked;
    const container = document.getElementById('selectedFilesList');
    
    try {
        const response = await fetch('/api/browse/scan', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                folder: currentPath,
                recursive: includeSubfolders
            })
        });
        
        const data = await response.json();
        
        if (data.error) {
            alert('Error: ' + data.error);
            return;
        }
        
        // Get target language from dropdown
        const targetLang = document.getElementById('language').value;
        // Extract language code (e.g., 'nl-NL' -> 'nl')
        const langCode = targetLang.split('-')[0];
        
        // Filter: only files that DON'T have SRT in target language
        selectedFolderFiles = data.files.filter(file => {
            // Check if file has SRT in target language
            const hasSRT = file.srt && file.srt.exists && 
                          file.srt.languages && 
                          file.srt.languages.includes(langCode);
            return !hasSRT; // Only include if SRT doesn't exist
        });
        
        const totalFiles = data.files.length;
        const skippedFiles = totalFiles - selectedFolderFiles.length;
        
        if (skippedFiles > 0) {
            console.log(`Skipped ${skippedFiles} file(s) that already have ${langCode} subtitles`);
        }
        
        if (selectedFolderFiles.length === 0) {
            alert(`All ${totalFiles} video file(s) in this folder already have ${langCode.toUpperCase()} subtitles.`);
            container.style.display = 'none';
            document.getElementById('transcribeBtn').disabled = true;
            return;
        }
        
        // Hide subfolders in file browser if recursive mode
        if (includeSubfolders) {
            const filteredFiles = allFiles.filter(f => f.type === 'file');
            renderFiles(filteredFiles);
        } else {
            renderFiles(allFiles);
        }
        
        // Show selected files list
        renderSelectedFilesList();
        
        document.getElementById('transcribeBtn').disabled = selectedFolderFiles.length === 0;
    } catch (error) {
        alert('Error scanning folder: ' + error.message);
    }
}

function renderSelectedFilesList() {
    const container = document.getElementById('selectedFilesList');
    
    if (selectedFolderFiles.length === 0) {
        container.style.display = 'none';
        return;
    }
    
    const includeSubfolders = document.getElementById('includeSubfolders').checked;
    
    // Expand list height if recursive mode
    if (includeSubfolders) {
        container.classList.add('expanded');
    } else {
        container.classList.remove('expanded');
    }
    
    container.style.display = 'block';
    
    const activeCount = selectedFolderFiles.length - excludedFiles.size;
    
    container.innerHTML = `
        <div class="selected-files-header">
            <h4>📋 Selected Files (${activeCount}/${selectedFolderFiles.length})</h4>
            <div class="selected-files-actions">
                <button class="add-to-queue" onclick="addSelectedToQueue()" ${activeCount === 0 ? 'disabled' : ''}>
                    ➕ Add to Queue
                </button>
                <button class="clear-selection" onclick="clearFolderSelection()">✕ Clear</button>
            </div>
        </div>
        <div class="selected-files-items">
        ${selectedFolderFiles.map((file, index) => {
            const isExcluded = excludedFiles.has(file.path);
            const srtStatus = file.has_srt ? '✓ Has SRT' : '';
            return `
                <div class="selected-file-item ${isExcluded ? 'excluded' : ''}">
                    <span class="selected-file-name" title="${file.path}">
                        ${file.name} ${srtStatus}
                    </span>
                    <div class="selected-file-actions">
                        <button class="btn-exclude ${isExcluded ? 'excluded' : ''}" onclick="toggleExcludeFile('${file.path.replace(/'/g, "\\'")}')">  
                            ${isExcluded ? '↩ Include' : '✕ Exclude'}
                        </button>
                    </div>
                </div>
            `;
        }).join('')}
        </div>
    `;
}

function toggleExcludeFile(filePath) {
    if (excludedFiles.has(filePath)) {
        excludedFiles.delete(filePath);
    } else {
        excludedFiles.add(filePath);
    }
    
    renderSelectedFilesList();
    
    // Update transcribe button state
    const activeCount = selectedFolderFiles.length - excludedFiles.size;
    
    // Disable button if all files are excluded
    document.getElementById('transcribeBtn').disabled = activeCount === 0;
}

function clearFolderSelection() {
    console.log('🧹 clearFolderSelection called');
    isFolderMode = false;
    selectedFolderFiles = [];
    excludedFiles.clear();
    
    document.getElementById('selectedFilesList').style.display = 'none';
    document.getElementById('subfolderLabel').style.display = 'none';
    
    console.log('📂 Restoring full file list, allFiles:', allFiles.length);
    // Restore full file list including subfolders
    renderFiles(allFiles);
    
    document.getElementById('transcribeBtn').disabled = true;
    console.log('✅ clearFolderSelection complete');
}

async function startTranscription() {
    // Prevent double-clicks
    const btn = document.getElementById('transcribeBtn');
    if (btn.disabled) return;
    
    if (!selectedFile && !isFolderMode) return;
    
    const language = document.getElementById('language').value;
    const overwrite = document.getElementById('overwrite').checked;
    const includeSubfolders = document.getElementById('includeSubfolders').checked;
    const enableSrtTranslation = localStorage.getItem('enableSrtTranslation') === 'true';
    
    btn.disabled = true;
    
    try {
        if (isFolderMode) {
            // Use add to queue button for folder mode
            alert('Please use the "➕ Add to Queue" button in the selected files list');
            btn.disabled = false;
            updateTranscribeButtonText();
            return;
        } else {
            // Check if this is SRT translation mode
            const isSrtFile = selectedFile.name.toLowerCase().endsWith('.srt');
            
            if (isSrtFile && enableSrtTranslation) {
                // SRT translation mode
                const translationModel = localStorage.getItem('translationModel') || 'nllb-200-1.3B';
                btn.textContent = 'Starting Translation...';
                
                const response = await fetch('/api/translate-srt', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        srt_path: selectedFile.path,
                        language: language,
                        translation_model: translationModel
                    })
                });
                
                const data = await response.json();
                
                if (data.error) {
                    alert('Error: ' + data.error);
                } else {
                    jobs[data.job_id] = {
                        id: data.job_id,
                        file: selectedFile.name,
                        status: 'pending'
                    };
                    updateJobList();
                    setTimeout(() => refreshFileStatusIndicators(), 100);
                }
            } else {
                // Normal video transcription
                const whisperModel = localStorage.getItem('whisperModel') || 'medium';
                const translationModel = localStorage.getItem('translationModel') || 'nllb-200-1.3B';
                const translationMethod = localStorage.getItem('translationMethod') || 'whisper';
                btn.textContent = 'Starting...';
                
                const response = await fetch('/api/transcribe', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        path: selectedFile.path,
                        language: language,
                        overwrite: overwrite,
                        whisper_model: whisperModel,
                        translation_model: translationModel,
                        translation_method: translationMethod
                    })
                });
                
                const data = await response.json();
                
                if (response.status === 409) {
                    // Conflict - existing files
                    const files = data.existing_files.join(', ');
                    const message = `${data.message}\n\nExisting files:\n${files}\n\nEnable the "Overwrite existing SRT files" checkbox to continue.`;
                    alert(message);
                } else if (data.error) {
                    alert('Error: ' + data.error);
                } else {
                    jobs[data.job_id] = {
                        id: data.job_id,
                        file: selectedFile.name,
                        status: 'pending'
                    };
                    updateJobList();
                    setTimeout(() => refreshFileStatusIndicators(), 100);
                }
            }
        }
    } catch (error) {
        alert('Error: ' + error.message);
    } finally {
        btn.disabled = false;
        updateTranscribeButtonText();
    }
}

async function startJobPolling() {
    setInterval(async () => {
        try {
            const response = await fetch('/api/jobs');
            const allJobs = await response.json();
            
            // Sync all active jobs from backend
            // Clear jobs that are no longer on backend
            const backendJobIds = new Set(allJobs.map(j => j.id));
            
            // Remove local jobs that are completed/cancelled and not on backend
            Object.keys(jobs).forEach(id => {
                const job = jobs[id];
                if (!backendJobIds.has(parseInt(id)) && 
                    (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled')) {
                    delete jobs[id];
                }
            });
            
            // Add/update all backend jobs
            const hadRunningJobs = Object.values(jobs).some(j => j.status === 'running');
            
            allJobs.forEach(job => {
                jobs[job.id] = job;
            });
            
            const hasRunningJobs = Object.values(jobs).some(j => j.status === 'running');
            
            updateJobList();
            
            // Refresh status indicators if job status changed
            if (hadRunningJobs !== hasRunningJobs || allJobs.some(j => j.status === 'completed' || j.status === 'failed')) {
                refreshFileStatusIndicators();
            }
        } catch (error) {
            console.error('Error polling jobs:', error);
        }
    }, 2000);
}

function updateJobList() {
    const jobList = document.getElementById('jobList');
    // Only show pending and running jobs in active queue
    const activeJobs = Object.values(jobs).filter(j => 
        j.status === 'pending' || j.status === 'running'
    );
    
    // Sort: running first, then pending by job ID
    activeJobs.sort((a, b) => {
        if (a.status === 'running' && b.status !== 'running') return -1;
        if (a.status !== 'running' && b.status === 'running') return 1;
        return a.id - b.id; // Lower ID = earlier in queue
    });
    
    if (activeJobs.length === 0) {
        jobList.innerHTML = '<p class="text-muted">No active jobs</p>';
        
        // Update status indicators when queue is empty
        refreshFileStatusIndicators();
        return;
    }
    
    jobList.innerHTML = activeJobs.map((job, index) => {
        // Extract filename from path
        const fileStr = typeof job.file === 'string' ? job.file : (job.file?.path || job.file?.name || String(job.file));
        const fileName = fileStr.split('/').pop();
        
        // Calculate queue position
        const runningCount = activeJobs.filter(j => j.status === 'running').length;
        const queuePosition = index + 1;
        let positionBadge = '';
        
        if (job.status === 'running') {
            positionBadge = `<span class="queue-position running">#${queuePosition}</span>`;
        } else if (job.status === 'pending') {
            positionBadge = `<span class="queue-position pending">#${queuePosition}</span>`;
        }
        
        // Format status with detailed message and percentage
        let statusText = job.status;
        if (job.status === 'running') {
            const statusMsg = job.status_message || job.language || '';
            const progressPct = job.progress || 0;
            statusText = `${statusMsg} (${progressPct}%)`;
        } else if (job.status === 'pending') {
            const waitingPosition = index - runningCount + 1;
            statusText = `Waiting (${waitingPosition} in queue) - ${job.language || ''}`;
        }
        
        // Determine if bump button should be shown
        // Don't show for: running jobs, or first pending job (position 1 after running)
        const isRunning = job.status === 'running';
        const isPending = job.status === 'pending';
        const showBump = isPending && index >= runningCount + 1; // Only show from 2nd pending onwards
        
        return `
        <div class="job-item">
            <div class="job-name" title="${job.file}">
                ${positionBadge}
                ${fileName}
            </div>
            <div class="job-status">${statusText}</div>
            ${job.status === 'running' || job.status === 'pending' ? `
                <div class="job-controls">
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: ${job.progress || 0}%"></div>
                    </div>
                    ${showBump ? `<button class="btn-bump" onclick="bumpJob(${job.id})" title="Move to front of queue">⬆️</button>` : ''}
                    <button class="btn-cancel" onclick="cancelJob(${job.id})">✕</button>
                </div>
            ` : ''}
            ${job.status === 'completed' ? '<div class="srt-status exists">✓ Complete</div>' : ''}
            ${job.status === 'failed' ? '<div class="srt-status missing">✗ Failed</div>' : ''}
            ${job.status === 'cancelled' ? '<div class="srt-status missing">✗ Cancelled</div>' : ''}
        </div>
    `}).join('');
    
    // Update file status indicators after job list update
    refreshFileStatusIndicators();
}

function updateBreadcrumb(path) {
    const breadcrumbPath = document.getElementById('breadcrumb-path');
    
    if (!path) {
        breadcrumbPath.innerHTML = '';
        return;
    }
    
    const parts = path.split('/');
    breadcrumbPath.innerHTML = ' / ' + parts.map((part, index) => {
        const fullPath = parts.slice(0, index + 1).join('/');
        return `<span class="breadcrumb-item" data-path="${fullPath}">${part}</span>`;
    }).join(' / ');
    
    // Add click listeners to breadcrumb parts
    breadcrumbPath.querySelectorAll('.breadcrumb-item').forEach(item => {
        item.addEventListener('click', () => {
            loadFiles(item.dataset.path);
        });
    });
}

function refreshFileStatusIndicators() {
    // Update status classes for all visible files
    const jobsArray = Object.values(jobs);
    console.log('🔄 Refreshing status indicators');
    console.log('📊 Total jobs:', jobsArray.length);
    console.log('📋 Jobs data:', jobsArray.map(j => ({ file: j.file, status: j.status })));
    
    // Debug: check ALL file-items
    const allFileItems = document.querySelectorAll('.file-item');
    console.log('📂 ALL file-items found:', allFileItems.length);
    
    // Fixed: dataset.type means the HTML attribute is data-type
    const fileItems = document.querySelectorAll('.file-item[data-type="file"]');
    console.log('📁 File items with data-type="file":', fileItems.length);
    
    // Video files have data-type="video"
    const videoItems = document.querySelectorAll('.file-item[data-type="video"]');
    console.log('📹 File items with data-type="video":', videoItems.length);
    
    // Use whichever selector finds items (file or video)
    const itemsToUpdate = fileItems.length > 0 ? fileItems : videoItems;
    console.log('✅ Using items:', itemsToUpdate.length);
    
    if (itemsToUpdate.length === 0 && allFileItems.length > 0) {
        console.warn('⚠️ No video/file items found!');
        if (allFileItems[0]) {
            console.log('First item dataset:', allFileItems[0].dataset);
        }
    }
    
    itemsToUpdate.forEach((div, index) => {
        const filePath = div.dataset.path;
        if (!filePath) return;
        
        const fileStr = typeof filePath === 'string' ? filePath : String(filePath);
        const fileName = fileStr.split('/').pop();
        
        if (index < 3) {
            console.log(`  File ${index}: path="${filePath}", name="${fileName}"`);
        }
        
        // Remove all status classes
        div.classList.remove('status-queued', 'status-processing', 'status-success', 'status-error', 'status-cancelled');
        
        // Find job for this file - try multiple matching strategies
        const fileJob = jobsArray.find(j => {
            // Direct path match
            if (j.file === filePath) return true;
            // Filename match
            if (j.file === fileName) return true;
            // Job file ends with our filename
            if (j.file.endsWith(fileName)) return true;
            // Our filepath ends with job file (in case job has partial path)
            if (filePath.endsWith(j.file)) return true;
            return false;
        });
        
        if (fileJob) {
            console.log('  ✅ MATCH:', fileName, 'status:', fileJob.status, 'job.file:', fileJob.file);
            if (fileJob.status === 'pending') {
                div.classList.add('status-queued');
                console.log('    → Added status-queued class');
            }
            else if (fileJob.status === 'running') {
                div.classList.add('status-processing');
                console.log('    → Added status-processing class');
            }
            else if (fileJob.status === 'completed') {
                div.classList.add('status-success');
                console.log('    → Added status-success class');
            }
            else if (fileJob.status === 'failed') {
                div.classList.add('status-error');
                console.log('    → Added status-error class');
            }
            else if (fileJob.status === 'cancelled') {
                div.classList.add('status-cancelled');
                console.log('    → Added status-cancelled class');
            }
            
            // Update queue button if exists
            const queueBtn = div.querySelector('.btn-add-to-queue');
            if (queueBtn) {
                const inQueue = fileJob.status === 'pending' || fileJob.status === 'running';
                if (inQueue) {
                    queueBtn.textContent = '✓ In Queue';
                    queueBtn.classList.add('in-queue');
                    queueBtn.disabled = true;
                } else {
                    queueBtn.textContent = '+ Queue';
                    queueBtn.classList.remove('in-queue');
                    queueBtn.disabled = false;
                }
            }
            
            // Verify class was added
            console.log('    → Classes after:', div.className);
        } else if (index < 3) {
            console.log('  ❌ NO MATCH for:', fileName);
            
            // Reset queue button to default state
            const queueBtn = div.querySelector('.btn-add-to-queue');
            if (queueBtn) {
                queueBtn.textContent = '+ Queue';
                queueBtn.classList.remove('in-queue');
                queueBtn.disabled = false;
            }
        }
    });
    
    console.log('✅ Status indicators refresh complete');
}

async function cancelJob(jobId) {
    try {
        const response = await fetch(`/api/jobs/${jobId}/cancel`, {
            method: 'POST'
        });
        
        if (response.ok) {
            console.log(`Job ${jobId} cancelled`);
        }
    } catch (error) {
        console.error('Error cancelling job:', error);
    }
}

async function bumpJob(jobId) {
    try {
        const response = await fetch(`/api/jobs/${jobId}/bump`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'}
        });
        
        if (!response.ok) {
            const contentType = response.headers.get('content-type');
            if (contentType && contentType.includes('application/json')) {
                const data = await response.json();
                alert(data.error || 'Failed to bump job');
            } else {
                alert('Server error: Unable to bump job');
            }
            return;
        }
        
        const data = await response.json();
        console.log(`Job ${jobId} bumped to front`);
        updateJobList();
    } catch (error) {
        console.error('Error bumping job:', error);
        alert('Error bumping job: ' + error.message);
    }
}

// History functions
async function openHistory() {
    document.getElementById('historyModal').style.display = 'flex';
    
    // Attach event listeners when modal opens (elements are now in DOM)
    const filterElement = document.getElementById('historyStatusFilter');
    const deleteBtn = document.getElementById('deleteFilteredBtn');
    const clearBtn = document.getElementById('clearHistoryBtn');
    const closeBtn = document.getElementById('closeHistory');
    
    if (filterElement && !filterElement.dataset.listenerAttached) {
        filterElement.addEventListener('change', filterHistory);
        filterElement.dataset.listenerAttached = 'true';
    }
    
    if (deleteBtn && !deleteBtn.dataset.listenerAttached) {
        deleteBtn.addEventListener('click', deleteFilteredHistory);
        deleteBtn.dataset.listenerAttached = 'true';
    }
    
    if (clearBtn && !clearBtn.dataset.listenerAttached) {
        clearBtn.addEventListener('click', clearHistory);
        clearBtn.dataset.listenerAttached = 'true';
    }
    
    if (closeBtn && !closeBtn.dataset.listenerAttached) {
        closeBtn.addEventListener('click', closeHistory);
        closeBtn.dataset.listenerAttached = 'true';
    }
    
    await loadHistory();
}

function closeHistory() {
    document.getElementById('historyModal').style.display = 'none';
}

async function loadHistory() {
    try {
        const response = await fetch('/api/history');
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const history = await response.json();
        
        // Get current filter - default to 'all' if element doesn't exist yet
        const filterElement = document.getElementById('historyStatusFilter');
        const filterValue = filterElement ? filterElement.value : 'all';
        
        // Filter history
        const filteredHistory = filterValue === 'all' 
            ? history 
            : history.filter(entry => entry.status === filterValue);
        
        const historyList = document.getElementById('historyList');
        
        if (filteredHistory.length === 0) {
            historyList.innerHTML = `<p class="text-muted">No ${filterValue === 'all' ? '' : filterValue + ' '}history available</p>`;
            return;
        }
        
        historyList.innerHTML = filteredHistory.map((entry, index) => {
            // Debug log to check translation_method
            if (index === 0) {
                console.log('History entry sample:', {
                    translation_method: entry.translation_method,
                    language: entry.language,
                    status: entry.status,
                    allKeys: Object.keys(entry)
                });
            }
            
            const statusClass = entry.status === 'completed' ? 'success' : 
                               entry.status === 'failed' ? 'failed' : 'cancelled';
            
            const statusIcon = entry.status === 'completed' ? '✓' : 
                              entry.status === 'failed' ? '✗' : '⊗';
            
            let resultHtml = '';
            if (entry.status === 'completed' && entry.result) {
                // Handle both array and string result formats
                let files = [];
                if (Array.isArray(entry.result)) {
                    files = entry.result;
                } else if (typeof entry.result === 'string') {
                    files = [entry.result];
                } else if (typeof entry.result === 'object' && entry.result.files) {
                    files = entry.result.files;
                }
                
                if (files.length > 0) {
                    resultHtml = `
                        <div class="history-result">
                            <strong>Generated files:</strong><br>
                            ${files.map(f => `📄 ${String(f).split('/').pop()}`).join('<br>')}
                        </div>
                    `;
                }
            } else if (entry.status === 'failed' && entry.error) {
                // Escape HTML and limit error message length
                const errorMsg = String(entry.error)
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#039;')
                    .substring(0, 500);
                
                // Handle both string and object file formats
                const fileStr = typeof entry.file === 'string' ? entry.file : 
                               (entry.file?.path || entry.file?.name || String(entry.file));
                const safeFile = fileStr.replace(/'/g, "\\'");
                const safeLang = String(entry.language).replace(/'/g, "\\'");
                
                resultHtml = `
                    <div class="history-error">
                        <strong>Error:</strong> ${errorMsg}${entry.error.length > 500 ? '...' : ''}
                        <button class="btn-retry" onclick="retryFailedJob('${safeFile}', '${safeLang}')">
                            🔄 Retry
                        </button>
                    </div>
                `;
            }
            
            // Handle both string and object file formats for display
            const fileStr = typeof entry.file === 'string' ? entry.file : 
                           (entry.file?.path || entry.file?.name || String(entry.file));
            const safeFileName = fileStr.replace(/</g, '&lt;').replace(/>/g, '&gt;');
            
            return `
                <div class="history-item-compact ${statusClass}">
                    <div class="history-compact-header" onclick="toggleHistoryDetails(${index})">
                        <span class="history-toggle" id="toggle-${index}">▶</span>
                        <div class="history-compact-info">
                            <div class="history-file-compact">${safeFileName}</div>
                            <div class="history-times">
                                <span class="history-time">Start: ${formatSystemDateTime(entry.started)}</span>
                                <span class="history-time">End: ${formatSystemDateTime(entry.completed)}</span>
                            </div>
                        </div>
                        <span class="history-status-compact ${statusClass}">${statusIcon} ${entry.status}</span>
                    </div>
                    <div class="history-details" id="details-${index}" style="display: none;">
                        <div class="history-meta">
                            <div class="history-meta-item">
                                <strong>Duration:</strong> ${entry.duration || 'N/A'}
                            </div>
                            <div class="history-meta-item">
                                <strong>Language:</strong> ${entry.language}
                                ${entry.detected_language ? ` → ${entry.detected_language}` : ''}
                            </div>
                            <div class="history-meta-item">
                                <strong>Method:</strong> ${getTranslationMethodLabel(entry.translation_method || 'whisper')}
                            </div>
                        </div>
                        ${resultHtml}
                    </div>
                </div>
            `;
        }).join('');
        
    } catch (error) {
        console.error('Error loading history:', error);
        document.getElementById('historyList').innerHTML = 
            `<p class="text-muted">Error loading history: ${error.message}</p>`;
    }
}

function toggleHistoryDetails(index) {
    const details = document.getElementById(`details-${index}`);
    const toggle = document.getElementById(`toggle-${index}`);
    
    if (details.style.display === 'none') {
        details.style.display = 'block';
        toggle.textContent = '▼';
    } else {
        details.style.display = 'none';
        toggle.textContent = '▶';
    }
}

function filterHistory() {
    loadHistory();
}

async function deleteFilteredHistory() {
    const filterValue = document.getElementById('historyStatusFilter').value;
    
    if (filterValue === 'all') {
        if (!confirm('Are you sure you want to delete ALL history?')) {
            return;
        }
    } else {
        if (!confirm(`Are you sure you want to delete all ${filterValue} jobs from history?`)) {
            return;
        }
    }
    
    try {
        const response = await fetch('/api/history/delete-filtered', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ status: filterValue })
        });
        
        if (response.ok) {
            await loadHistory();
            alert(`✓ Deleted ${filterValue === 'all' ? 'all' : filterValue} history entries`);
        } else {
            alert('Error deleting history');
        }
    } catch (error) {
        console.error('Error deleting filtered history:', error);
        alert('Error deleting history');
    }
}

async function clearQueue() {
    const activeJobs = Object.values(jobs).filter(j => j.status === 'pending' || j.status === 'running');
    
    if (activeJobs.length === 0) {
        alert('No active jobs in queue');
        return;
    }
    
    if (!confirm(`Are you sure you want to clear ${activeJobs.length} job(s) from the queue?`)) {
        return;
    }
    
    try {
        // Cancel all active jobs
        const cancelPromises = activeJobs.map(job => 
            fetch(`/api/jobs/${job.id}/cancel`, { method: 'POST' })
        );
        
        await Promise.all(cancelPromises);
        
        // Remove from local jobs object
        activeJobs.forEach(job => {
            delete jobs[job.id];
        });
        
        console.log(`✓ Cleared ${activeJobs.length} job(s) from queue`);
        
        // Update UI
        updateJobList();
        
        // Refresh status indicators after clearing queue
        setTimeout(() => refreshFileStatusIndicators(), 100);
        
        alert(`✓ Cleared ${activeJobs.length} job(s) from queue`);
    } catch (error) {
        console.error('Error clearing queue:', error);
        alert('Error clearing queue: ' + error.message);
    }
}

async function clearHistory() {
    if (!confirm('Are you sure you want to clear all job history?')) {
        return;
    }
    
    try {
        const response = await fetch('/api/history/clear', {
            method: 'POST'
        });
        
        const data = await response.json();
        
        if (response.ok && data.success) {
            console.log('✓ History cleared successfully');
            await loadHistory();
            alert('✓ History cleared successfully');
        } else {
            console.error('Failed to clear history:', data);
            alert('Failed to clear history');
        }
    } catch (error) {
        console.error('Error clearing history:', error);
        alert('Error clearing history: ' + error.message);
    }
}

async function retryFailedJob(filePath, language) {
    const fileStr = typeof filePath === 'string' ? filePath : (filePath.path || filePath.name || String(filePath));
    if (!confirm(`Retry transcription for:\n${fileStr.split('/').pop()}\n\nLanguage: ${language}`)) {
        return;
    }
    
    try {
        const overwrite = document.getElementById('overwrite').checked;
        const whisperModel = localStorage.getItem('whisperModel') || 'medium';
        const translationModel = localStorage.getItem('translationModel') || 'nllb-200-1.3B';
        
        const response = await fetch('/api/transcribe', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                path: filePath,
                language: language,
                overwrite: overwrite,
                whisper_model: whisperModel,
                translation_model: translationModel
            })
        });
        
        const data = await response.json();
        
        if (response.status === 409) {
            const files = data.existing_files.join(', ');
            const message = `${data.message}\n\nExisting files:\n${files}\n\nEnable the "Overwrite existing SRT files" checkbox to continue.`;
            alert(message);
        } else if (data.error) {
            alert('Error: ' + data.error);
        } else {
            // Remove any old failed job for this file
            for (const jobId in jobs) {
                if (jobs[jobId].file === filePath && jobs[jobId].status === 'failed') {
                    delete jobs[jobId];
                }
            }
            
            // Add new pending job
            jobs[data.job_id] = {
                id: data.job_id,
                file: filePath,
                status: 'pending',
                language: language
            };
            updateJobList();
            setTimeout(() => refreshFileStatusIndicators(), 100);
            
            // Close history modal and show success
            document.getElementById('historyModal').style.display = 'none';
            alert('✓ Job added to queue');
        }
    } catch (error) {
        alert('Error retrying job: ' + error.message);
    }
}

function formatSystemDateTime(isoString) {
    if (!isoString) return 'N/A';
    
    const date = new Date(isoString);
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    const seconds = String(date.getSeconds()).padStart(2, '0');
    
    return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

function formatDateTime(isoString) {
    if (!isoString) return 'N/A';
    
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);
    
    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;
    
    return date.toLocaleDateString();
}

async function addFileToQueue(filePath) {
    // Check if already in queue
    const existingJob = Object.values(jobs).find(j => 
        j.file === filePath && (j.status === 'pending' || j.status === 'running')
    );
    
    if (existingJob) {
        alert('This file is already in the queue');
        return;
    }
    
    try {
        const language = document.getElementById('language').value;
        const overwrite = document.getElementById('overwrite').checked;
        const whisperModel = localStorage.getItem('whisperModel') || 'medium';
        const translationModel = localStorage.getItem('translationModel') || 'nllb-200-1.3B';
        const chunkLength = localStorage.getItem('chunkLength') || '30';
        
        const response = await fetch('/api/transcribe', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                path: filePath,
                language: language,
                overwrite: overwrite,
                whisper_model: whisperModel,
                translation_model: translationModel,
                chunk_length: parseInt(chunkLength)
            })
        });
        
        const data = await response.json();
        
        if (response.status === 409) {
            const files = data.existing_files.join(', ');
            const message = `${data.message}\n\nExisting files:\n${files}\n\nEnable the "Overwrite existing SRT files" checkbox to continue.`;
            alert(message);
        } else if (data.error) {
            alert('Error: ' + data.error);
        } else {
            jobs[data.job_id] = {
                id: data.job_id,
                file: filePath,
                status: 'pending'
            };
            updateJobList();
            setTimeout(() => refreshFileStatusIndicators(), 100);
        }
    } catch (error) {
        alert('Error: ' + error.message);
    }
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    const value = (bytes / Math.pow(k, i)).toFixed(2);
    // Pad to ensure alignment (max 99.99 XX)
    return value.padStart(5, ' ') + ' ' + sizes[i];
}

// SRT Filter functionality
let filteredMissingFiles = [];

async function filterBySRT() {
    const filterValue = document.getElementById('srtFilter').value;
    const addFilteredBtn = document.getElementById('addFilteredBtn');
    
    // Save filter preference to localStorage
    localStorage.setItem('srtFilter', filterValue);
    
    if (filterValue === 'all') {
        // Show all files
        addFilteredBtn.style.display = 'none';
        displayedFiles = allFiles;
        renderIndex = 0;
        renderFiles(allFiles);
    } else if (filterValue === 'missing') {
        // Filter by missing target SRT
        addFilteredBtn.style.display = 'inline-block';
        
        // Get target language from settings (use default language preference)
        const targetLanguage = localStorage.getItem('defaultLanguage') || 
                               document.getElementById('language')?.value || 
                               'nl-NL';
        
        try {
            const response = await fetch('/api/missing-srt', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ 
                    language: targetLanguage,
                    path: currentPath
                })
            });
            
            console.log('Missing SRT response status:', response.status);
            
            if (!response.ok) {
                const errorText = await response.text();
                console.error('API error response:', errorText);
                throw new Error(`Failed to scan for missing SRT files: ${response.status} ${errorText}`);
            }
            
            const data = await response.json();
            console.log('Missing SRT data:', data);
            filteredMissingFiles = data.files || [];
            
            // Filter displayedFiles to only show files missing target SRT
            const missingPaths = new Set(filteredMissingFiles.map(f => f.path));
            displayedFiles = allFiles.filter(file => {
                if (file.type === 'folder') return true; // Keep folders
                const fullPath = currentPath ? `${currentPath}/${file.name}` : file.name;
                return missingPaths.has(fullPath);
            });
            
            renderIndex = 0;
            
            // Update status message
            if (filteredMissingFiles.length === 0) {
                const fileList = document.getElementById('fileList');
                fileList.innerHTML = `<p class="text-muted" style="padding: 20px; text-align: center;">All files have ${data.language_code.toUpperCase()} subtitles ✓</p>`;
            } else {
                // Render filtered files
                renderFiles(displayedFiles);
            }
            
        } catch (error) {
            console.error('Error filtering by missing SRT:', error);
            console.error('Error details:', {
                message: error.message,
                stack: error.stack,
                name: error.name
            });
            alert('Error scanning for missing SRT files: ' + (error.message || 'Unknown error'));
        }
    }
}

async function addFilteredToQueue() {
    if (filteredMissingFiles.length === 0) {
        alert('No files to add');
        return;
    }
    
    console.log('🎬 Adding filtered files to queue:', filteredMissingFiles);
    
    // Get target language from settings
    const targetLanguage = localStorage.getItem('defaultLanguage') || 
                           document.getElementById('language')?.value || 
                           'nl-NL';
    const whisperModel = localStorage.getItem('whisperModel') || 'medium';
    const translationModel = localStorage.getItem('translationModel') || 'nllb-200-1.3B';
    const translationMethod = localStorage.getItem('translationMethod') || 'whisper';
    const chunkLength = parseInt(localStorage.getItem('chunkLength')) || 30;
    
    if (!confirm(`Add ${filteredMissingFiles.length} files to transcription queue for ${targetLanguage}?`)) {
        return;
    }
    
    const filePaths = filteredMissingFiles.map(f => f.path);
    console.log('📁 File paths to queue:', filePaths);
    
    try {
        const requestBody = {
            files: filePaths,
            language: targetLanguage,
            overwrite: false,
            whisper_model: whisperModel,
            translation_model: translationModel,
            translation_method: translationMethod,
            chunk_length: chunkLength
        };
        
        console.log('📤 Sending batch request:', requestBody);
        
        const response = await fetch('/api/transcribe/batch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(requestBody)
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to start batch transcription');
        }
        
        const data = await response.json();
        console.log('Batch transcribe response:', data);
        alert(`✓ Added ${data.count || data.jobs?.length || 0} jobs to queue`);
        
        // Reset filter
        document.getElementById('srtFilter').value = 'all';
        filterBySRT();
        
    } catch (error) {
        console.error('Error adding filtered files to queue:', error);
        alert('Error: ' + error.message);
    }
}

// Missing SRT functionality (legacy - kept for backward compatibility)
let missingFiles = [];

async function refreshMissingSRT() {
    const targetLanguage = document.getElementById('missingLanguageFilter').value;
    const missingList = document.getElementById('missingList');
    const processBtn = document.getElementById('processMissingBtn');
    
    missingList.innerHTML = '<p class="text-muted">Scanning...</p>';
    processBtn.disabled = true;
    
    try {
        const response = await fetch('/api/missing-srt', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ 
                language: targetLanguage,
                path: currentPath  // Use current browse directory
            })
        });
        
        if (!response.ok) {
            throw new Error('Failed to scan for missing SRT files');
        }
        
        const data = await response.json();
        missingFiles = data.files || [];
        
        if (missingFiles.length === 0) {
            const pathInfo = currentPath ? ` in ${currentPath}` : '';
            missingList.innerHTML = `<p class="text-muted">All files${pathInfo} have ${data.language_code.toUpperCase()} subtitles ✓</p>`;
            processBtn.disabled = true;
            return;
        }
        
        // Group by folder for better overview
        const byFolder = {};
        missingFiles.forEach(file => {
            const folder = file.folder || '/';
            if (!byFolder[folder]) byFolder[folder] = [];
            byFolder[folder].push(file);
        });
        
        let html = `<div style="margin-bottom: 8px; font-size: 0.85rem; color: var(--warning);">
            <strong>${missingFiles.length}</strong> files missing <strong>${data.language_code.toUpperCase()}</strong> subtitles
            ${currentPath ? `<div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 2px;">📁 in ${currentPath}</div>` : ''}
        </div>`;
        
        // Show up to 20 files with folder grouping
        const displayLimit = 20;
        let count = 0;
        
        for (const [folder, files] of Object.entries(byFolder)) {
            if (count >= displayLimit) break;
            
            html += `<div style="margin-bottom: 6px; font-size: 0.75rem; color: var(--text-muted); border-top: 1px solid var(--border); padding-top: 4px;">
                📁 ${folder}
            </div>`;
            
            for (const file of files) {
                if (count >= displayLimit) break;
                
                const enBadge = file.has_en_srt ? '<span style="background: var(--success); color: white; padding: 1px 4px; border-radius: 3px; font-size: 0.65rem; margin-left: 4px;">EN</span>' : '';
                
                html += `<div style="font-size: 0.8rem; padding: 3px 0; padding-left: 10px;">
                    📄 ${file.name} ${enBadge}
                </div>`;
                
                count++;
            }
        }
        
        if (missingFiles.length > displayLimit) {
            html += `<div style="margin-top: 6px; font-size: 0.75rem; font-style: italic; color: var(--text-muted);">
                ... and ${missingFiles.length - displayLimit} more
            </div>`;
        }
        
        missingList.innerHTML = html;
        processBtn.disabled = false;
        
    } catch (error) {
        console.error('Error scanning for missing SRT:', error);
        missingList.innerHTML = '<p class="text-muted" style="color: var(--danger);">Error scanning files</p>';
        processBtn.disabled = true;
    }
}

async function processMissingSRT() {
    if (missingFiles.length === 0) return;
    
    const targetLanguage = document.getElementById('missingLanguageFilter').value;
    const whisperModel = localStorage.getItem('whisperModel') || 'medium';
    const translationModel = localStorage.getItem('translationModel') || 'nllb-200-1.3B';
    const translationMethod = localStorage.getItem('translationMethod') || 'whisper';
    const chunkLength = parseInt(localStorage.getItem('chunkLength')) || 30;
    
    if (!confirm(`Add ${missingFiles.length} files to transcription queue for ${targetLanguage}?`)) {
        return;
    }
    
    const filePaths = missingFiles.map(f => f.path);
    
    try {
        const response = await fetch('/api/transcribe/batch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                files: filePaths,
                language: targetLanguage,
                overwrite: false, // Don't overwrite existing SRT
                whisper_model: whisperModel,
                translation_model: translationModel,
                translation_method: translationMethod,
                chunk_length: chunkLength
            })
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to start batch transcription');
        }
        
        const data = await response.json();
        alert(`✓ Added ${data.queued} jobs to queue`);
        
        // Clear the list and refresh
        missingFiles = [];
        document.getElementById('missingList').innerHTML = '<p class="text-muted">Click refresh to scan</p>';
        document.getElementById('processMissingBtn').disabled = true;
        
    } catch (error) {
        console.error('Error processing missing SRT:', error);
        alert('Error: ' + error.message);
    }
}
