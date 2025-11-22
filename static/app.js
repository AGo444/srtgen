let currentPath = '';
let selectedFile = null;
let jobs = {};
let allFiles = []; // Store all files for filtering
let isFolderMode = false; // Track folder selection mode
let selectedFolderFiles = []; // Files selected for batch processing
let excludedFiles = new Set(); // Files excluded from batch processing
let hasSubfolders = false; // Whether current folder has subfolders

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    loadSettings();
    
    // Restore last path from localStorage or load root
    const lastPath = localStorage.getItem('currentPath') || '';
    loadFiles(lastPath);
    
    setupEventListeners();
    startJobPolling();
});

function setupEventListeners() {
    document.getElementById('transcribeBtn').addEventListener('click', startTranscription);
    document.getElementById('selectFolderBtn').addEventListener('click', selectCurrentFolder);
    document.getElementById('saveSettingsBtn').addEventListener('click', saveSettings);
    document.getElementById('settingsToggle').addEventListener('click', openSettings);
    document.getElementById('closeSettings').addEventListener('click', closeSettings);
    document.getElementById('historyToggle').addEventListener('click', openHistory);
    document.getElementById('closeHistory').addEventListener('click', closeHistory);
    document.getElementById('clearHistoryBtn').addEventListener('click', clearHistory);
    
    // Close modals when clicking outside
    window.addEventListener('click', (e) => {
        const settingsModal = document.getElementById('settingsModal');
        const historyModal = document.getElementById('historyModal');
        if (e.target === settingsModal) {
            closeSettings();
        }
        if (e.target === historyModal) {
            closeHistory();
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
    
    // Sync default language to transcription language
    document.getElementById('defaultLanguage').addEventListener('change', (e) => {
        document.getElementById('language').value = e.target.value;
    });
    
    // Rescan folder when subfolder checkbox changes
    document.getElementById('includeSubfolders').addEventListener('change', () => {
        if (isFolderMode) {
            selectCurrentFolder();
        }
    });
}

function openSettings() {
    document.getElementById('settingsModal').style.display = 'flex';
}

function closeSettings() {
    document.getElementById('settingsModal').style.display = 'none';
}

function loadSettings() {
    const defaultLang = localStorage.getItem('defaultLanguage') || 'nl-NL';
    document.getElementById('defaultLanguage').value = defaultLang;
    document.getElementById('language').value = defaultLang;
}

function saveSettings() {
    const defaultLang = document.getElementById('defaultLanguage').value;
    localStorage.setItem('defaultLanguage', defaultLang);
    document.getElementById('language').value = defaultLang;
    
    // Visual feedback
    const btn = document.getElementById('saveSettingsBtn');
    const originalText = btn.textContent;
    btn.textContent = '✓ Saved!';
    setTimeout(() => {
        btn.textContent = originalText;
        closeSettings();
    }, 1000);
}

async function loadFiles(path) {
    currentPath = path;
    
    // Save current path to localStorage
    localStorage.setItem('currentPath', path);
    
    const fileList = document.getElementById('fileList');
    const searchInput = document.getElementById('searchInput');
    
    fileList.innerHTML = '<div class="loading">Loading files...</div>';
    searchInput.value = ''; // Clear search when navigating
    
    try {
        const response = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
        const data = await response.json();
        
        if (data.error) {
            fileList.innerHTML = `<div class="loading">Error: ${data.error}</div>`;
            return;
        }
        
        allFiles = data.items; // Store for filtering
        renderFiles(allFiles);
        updateBreadcrumb(path);
        
        // Check if folder has subfolders and video files
        hasSubfolders = data.items.some(item => item.type === 'directory');
        const hasVideos = data.items.some(item => item.type === 'file');
        
        // Enable folder button if we're in a directory with videos
        const folderBtn = document.getElementById('selectFolderBtn');
        folderBtn.disabled = !path || !hasVideos;
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
    
    if (items.length === 0) {
        fileList.innerHTML = '<div class="loading">No files found</div>';
        return;
    }
    
    fileList.innerHTML = '';
    
    items.forEach(item => {
        const div = document.createElement('div');
        div.className = 'file-item';
        div.dataset.path = item.path;
        div.dataset.type = item.type;
        
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
            
            div.innerHTML = `
                <div class="file-info">
                    <div class="file-name">🎬 ${item.name}</div>
                    <div class="file-meta">
                        ${formatFileSize(item.size)}
                        ${srtStatus}
                    </div>
                </div>
            `;
            div.addEventListener('click', () => selectFile(item));
        }
        
        fileList.appendChild(div);
    });
}

function selectFile(file) {
    selectedFile = file;
    isFolderMode = false;
    selectedFolderFiles = [];
    excludedFiles.clear();
    
    // Hide folder selection UI
    document.getElementById('selectedFilesList').style.display = 'none';
    document.getElementById('subfolderLabel').style.display = 'none';
    
    // Update UI
    document.querySelectorAll('.file-item').forEach(item => {
        item.classList.remove('selected');
    });
    
    const selectedElement = document.querySelector(`[data-path="${file.path}"]`);
    if (selectedElement) {
        selectedElement.classList.add('selected');
    }
    
    // Update sidebar
    const selectedFileDiv = document.getElementById('selectedFile');
    const srtLanguages = (file.srt.languages || []).join(', ') || 'none';
    const srtInfo = file.srt.exists
        ? `<span class="srt-status exists">Has SRT: ${srtLanguages}</span>`
        : `<span class="srt-status missing">No SRT</span>`;
    
    selectedFileDiv.innerHTML = `
        <div><strong>${file.name}</strong></div>
        <div class="file-meta">Size: ${formatFileSize(file.size)}</div>
        <div class="file-meta">${srtInfo}</div>
    `;
    
    document.getElementById('transcribeBtn').disabled = false;
}

async function selectCurrentFolder() {
    if (!currentPath) return;
    
    isFolderMode = true;
    selectedFile = null;
    excludedFiles.clear();
    
    // Show/hide subfolder checkbox based on whether subfolders exist
    const subfolderLabel = document.getElementById('subfolderLabel');
    subfolderLabel.style.display = hasSubfolders ? 'flex' : 'none';
    
    // Clear file selection in file browser
    document.querySelectorAll('.file-item').forEach(item => {
        item.classList.remove('selected');
    });
    
    // Scan for video files
    const includeSubfolders = document.getElementById('includeSubfolders').checked;
    
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
        
        selectedFolderFiles = data.files;
        
        // Show selected files list
        renderSelectedFilesList();
        
        // Update sidebar
        const selectedFileDiv = document.getElementById('selectedFile');
        selectedFileDiv.innerHTML = `
            <div><strong>📁 Folder Selected</strong></div>
            <div class="file-meta">Path: ${currentPath}</div>
            <div class="file-meta">${selectedFolderFiles.length} video file(s) found</div>
            <div class="file-meta" style="color: var(--primary);">${selectedFolderFiles.length - excludedFiles.size} will be processed</div>
        `;
        
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
    
    container.style.display = 'block';
    
    const activeCount = selectedFolderFiles.length - excludedFiles.size;
    
    container.innerHTML = `
        <div class="selected-files-header">
            <h4>📋 Selected Files (${activeCount}/${selectedFolderFiles.length})</h4>
            <button class="clear-selection" onclick="clearFolderSelection()">✕ Clear</button>
        </div>
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
    `;
}

function toggleExcludeFile(filePath) {
    if (excludedFiles.has(filePath)) {
        excludedFiles.delete(filePath);
    } else {
        excludedFiles.add(filePath);
    }
    
    renderSelectedFilesList();
    
    // Update sidebar count
    const selectedFileDiv = document.getElementById('selectedFile');
    const activeCount = selectedFolderFiles.length - excludedFiles.size;
    selectedFileDiv.innerHTML = `
        <div><strong>📁 Folder Selected</strong></div>
        <div class="file-meta">Path: ${currentPath}</div>
        <div class="file-meta">${selectedFolderFiles.length} video file(s) found</div>
        <div class="file-meta" style="color: var(--primary);">${activeCount} will be processed</div>
    `;
    
    // Disable button if all files are excluded
    document.getElementById('transcribeBtn').disabled = activeCount === 0;
}

function clearFolderSelection() {
    isFolderMode = false;
    selectedFolderFiles = [];
    excludedFiles.clear();
    
    document.getElementById('selectedFilesList').style.display = 'none';
    document.getElementById('subfolderLabel').style.display = 'none';
    
    const selectedFileDiv = document.getElementById('selectedFile');
    selectedFileDiv.innerHTML = '<div class="no-selection">No file selected</div>';
    
    document.getElementById('transcribeBtn').disabled = true;
}

async function startTranscription() {
    if (!selectedFile && !isFolderMode) return;
    
    const language = document.getElementById('language').value;
    const overwrite = document.getElementById('overwrite').checked;
    const includeSubfolders = document.getElementById('includeSubfolders').checked;
    const btn = document.getElementById('transcribeBtn');
    
    btn.disabled = true;
    
    try {
        if (isFolderMode) {
            // Batch process folder with selected files only
            btn.textContent = 'Adding jobs...';
            
            // Filter out excluded files
            const filesToProcess = selectedFolderFiles
                .filter(f => !excludedFiles.has(f.path))
                .map(f => f.path);
            
            if (filesToProcess.length === 0) {
                alert('No files selected for processing');
                btn.disabled = false;
                btn.textContent = 'Generate Subtitles';
                return;
            }
            
            const response = await fetch('/api/transcribe/batch', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    files: filesToProcess,
                    language: language,
                    overwrite: overwrite
                })
            });
            
            const data = await response.json();
            
            if (data.error) {
                alert('Error: ' + data.error);
            } else {
                // Add all jobs to the queue
                data.jobs.forEach(job => {
                    jobs[job.job_id] = {
                        id: job.job_id,
                        file: job.file,
                        status: 'pending'
                    };
                });
                
                alert(`Added ${data.jobs.length} file(s) to the queue!`);
                updateJobList();
            }
        } else {
            // Single file transcription
            btn.textContent = 'Starting...';
            
            const response = await fetch('/api/transcribe', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    path: selectedFile.path,
                    language: language,
                    overwrite: overwrite
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
            }
        }
    } catch (error) {
        alert('Error: ' + error.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Generate Subtitles';
    }
}

async function startJobPolling() {
    setInterval(async () => {
        if (Object.keys(jobs).length === 0) return;
        
        try {
            const response = await fetch('/api/jobs');
            const allJobs = await response.json();
            
            allJobs.forEach(job => {
                jobs[job.id] = job;
            });
            
            updateJobList();
        } catch (error) {
            console.error('Error polling jobs:', error);
        }
    }, 2000);
}

function updateJobList() {
    const jobList = document.getElementById('jobList');
    const activeJobs = Object.values(jobs).filter(j => j.status !== 'completed');
    
    if (activeJobs.length === 0) {
        jobList.innerHTML = '<p class="text-muted">No active jobs</p>';
        return;
    }
    
    jobList.innerHTML = activeJobs.map(job => `
        <div class="job-item">
            <div class="job-name">${job.file}</div>
            <div class="job-status">
                ${job.status} ${job.language || ''}
                ${job.status_message ? `<br><small>${job.status_message}</small>` : ''}
            </div>
            ${job.status === 'running' || job.status === 'pending' ? `
                <div class="progress-bar">
                    <div class="progress-fill" style="width: ${job.progress || 0}%"></div>
                </div>
                <button class="btn-cancel" onclick="cancelJob(${job.id})">✕ Cancel</button>
            ` : ''}
            ${job.status === 'completed' ? '<div class="srt-status exists">✓ Complete</div>' : ''}
            ${job.status === 'failed' ? '<div class="srt-status missing">✗ Failed</div>' : ''}
            ${job.status === 'cancelled' ? '<div class="srt-status missing">✗ Cancelled</div>' : ''}
        </div>
    `).join('');
    
    // Remove completed jobs after 5 seconds
    Object.keys(jobs).forEach(id => {
        if (jobs[id].status === 'completed') {
            setTimeout(() => {
                delete jobs[id];
                updateJobList();
                loadFiles(currentPath); // Refresh file list
            }, 5000);
        }
    });
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

// History functions
async function openHistory() {
    document.getElementById('historyModal').style.display = 'flex';
    await loadHistory();
}

function closeHistory() {
    document.getElementById('historyModal').style.display = 'none';
}

async function loadHistory() {
    try {
        const response = await fetch('/api/history');
        const history = await response.json();
        
        const historyList = document.getElementById('historyList');
        
        if (history.length === 0) {
            historyList.innerHTML = '<p class="text-muted">No history available</p>';
            return;
        }
        
        historyList.innerHTML = history.map(entry => {
            const statusClass = entry.status === 'completed' ? 'success' : 
                               entry.status === 'failed' ? 'failed' : 'cancelled';
            
            let resultHtml = '';
            if (entry.status === 'completed' && entry.result) {
                const files = Array.isArray(entry.result) ? entry.result : [entry.result];
                resultHtml = `
                    <div class="history-result">
                        <strong>Generated files:</strong><br>
                        ${files.map(f => `📄 ${f.split('/').pop()}`).join('<br>')}
                    </div>
                `;
            } else if (entry.status === 'failed' && entry.error) {
                resultHtml = `
                    <div class="history-error">
                        <strong>Error:</strong> ${entry.error}
                    </div>
                `;
            }
            
            return `
                <div class="history-item ${statusClass}">
                    <div class="history-item-header">
                        <div class="history-file">📁 ${entry.file}</div>
                        <span class="history-status ${statusClass}">${entry.status}</span>
                    </div>
                    <div class="history-meta">
                        <div class="history-meta-item">
                            <strong>Started:</strong> ${formatDateTime(entry.started)}
                        </div>
                        <div class="history-meta-item">
                            <strong>Completed:</strong> ${formatDateTime(entry.completed)}
                        </div>
                        <div class="history-meta-item">
                            <strong>Duration:</strong> ${entry.duration || 'N/A'}
                        </div>
                        <div class="history-meta-item">
                            <strong>Language:</strong> ${entry.language}
                            ${entry.detected_language ? ` → ${entry.detected_language}` : ''}
                        </div>
                    </div>
                    ${resultHtml}
                </div>
            `;
        }).join('');
        
    } catch (error) {
        console.error('Error loading history:', error);
        document.getElementById('historyList').innerHTML = 
            '<p class="text-muted">Error loading history</p>';
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
        
        if (response.ok) {
            await loadHistory();
        }
    } catch (error) {
        console.error('Error clearing history:', error);
    }
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
    
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}
