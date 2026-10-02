/**
 * Subtitle Detector
 * 
 * Detects and extracts subtitles from video players using:
 * 1. HTML5 TextTrack API (WebVTT/TTML tracks)
 * 2. DOM MutationObserver (for custom subtitle overlays)
 * 
 * Supports:
 * - Standard HTML5 video with <track> elements
 * - YouTube (native captions)
 * - Netflix (custom DOM)
 * - Generic DOM-based subtitle containers
 */

class SubtitleDetector {
  constructor() {
    // Detection state
    this.active = false;
    this.currentMethod = null; // 'texttrack' or 'dom'
    this.videoElement = null;
    this.subtitleContainer = null;
    
    // Observers
    this.domObserver = null;
    this.videoObserver = null;
    
    // Tracking
    this.activeCues = new Map(); // cueId -> cue data
    this.lastCueText = '';
    this.cueCounter = 0;
    
    // Callbacks
    this.onCueCallback = null;
    this.onCueEndCallback = null;
    
    // Site-specific selectors
    this.siteAdapters = {
      'youtube.com': {
        selector: '.ytp-caption-segment',
        container: '.ytp-caption-window-container',
        mode: 'dom',
      },
      'netflix.com': {
        selector: '.player-timedtext-text-container span',
        container: '.player-timedtext',
        mode: 'dom',
      },
      'primevideo.com': {
        selector: '.atvwebplayersdk-captions-text',
        container: '.atvwebplayersdk-captions-overlay',
        mode: 'dom',
      },
      'disneyplus.com': {
        selector: '.btm-media-overlays-container span',
        container: '.btm-media-overlays-container',
        mode: 'dom',
      },
      'hbomax.com': {
        selector: '[data-testid="player-subtitles"] span',
        container: '[data-testid="player-subtitles"]',
        mode: 'dom',
      },
      'max.com': {
        selector: '[data-testid="player-subtitles"] span',
        container: '[data-testid="player-subtitles"]',
        mode: 'dom',
      },
      'twitch.tv': {
        selector: '.video-chat__message-list-wrapper span',
        container: '.video-player__overlay',
        mode: 'dom',
      },
      // Chinese streaming sites
      'yfsp.tv': {
        selector: '.dplayer-subtitle span, .dplayer-subtitle, .subtitle-text, [class*="subtitle"] span',
        container: '.dplayer-video-wrap, .dplayer, .player-container, [class*="player"]',
        mode: 'dom',
      },
      'bilibili.com': {
        selector: '.bilibili-player-video-subtitle span, .subtitle-text',
        container: '.bilibili-player-video-subtitle, .bpx-player-subtitle-panel',
        mode: 'dom',
      },
      'iqiyi.com': {
        selector: '.iqp-subtitle span, [class*="subtitle"] span',
        container: '.iqp-subtitle, [class*="subtitle-container"]',
        mode: 'dom',
      },
      'youku.com': {
        selector: '.subtitle-text span, [class*="subtitle"] span',
        container: '[class*="subtitle"]',
        mode: 'dom',
      },
      'v.qq.com': {
        selector: '.txp_subtitle_content span',
        container: '.txp_subtitle',
        mode: 'dom',
      },
      // Korean streaming
      'viu.com': {
        selector: '.vjs-text-track-display span, .vjs-text-track-cue div',
        container: '.vjs-text-track-display',
        mode: 'dom',
      },
      // Japanese streaming
      'abema.tv': {
        selector: '[class*="subtitle"] span, .com-a-Video__subtitle',
        container: '[class*="subtitle"], .com-a-Video',
        mode: 'dom',
      },
      // General video.js players
      'default': {
        selector: '.vjs-text-track-display div, .vjs-text-track-cue, .subtitle, .caption, [class*="subtitle"], [class*="caption"], .dplayer-subtitle',
        container: 'video, .video-container, .player, [class*="player"]',
        mode: 'auto',
      }
    };

    // Manual selector override (can be set by user)
    this.manualSelector = null;
    this.manualContainer = null;
  }
  
  /**
   * Start detecting subtitles.
   */
  start() {
    if (this.active) return;
    
    console.log('[Detector] Starting subtitle detection');
    this.active = true;
    
    // Try TextTrack API first
    const foundTextTrack = this._setupTextTrackDetection();
    
    if (!foundTextTrack) {
      // Fall back to DOM observation
      this._setupDOMDetection();
    }
    
    // Watch for new video elements
    this._setupVideoObserver();
  }
  
  /**
   * Stop detecting subtitles.
   */
  stop() {
    console.log('[Detector] Stopping subtitle detection');
    this.active = false;
    
    // Clean up TextTrack listeners
    if (this.videoElement) {
      const tracks = this.videoElement.textTracks;
      for (let i = 0; i < tracks.length; i++) {
        tracks[i].removeEventListener('cuechange', this._handleCueChange);
      }
    }
    
    // Clean up observers
    if (this.domObserver) {
      this.domObserver.disconnect();
      this.domObserver = null;
    }
    
    if (this.videoObserver) {
      this.videoObserver.disconnect();
      this.videoObserver = null;
    }
    
    this.activeCues.clear();
    this.lastCueText = '';
    this.currentMethod = null;
  }
  
  /**
   * Set callback for when a new cue appears.
   */
  onCue(callback) {
    this.onCueCallback = callback;
  }
  
  /**
   * Set callback for when a cue ends.
   */
  onCueEnd(callback) {
    this.onCueEndCallback = callback;
  }
  
  /**
   * Get the current video element.
   */
  getVideoElement() {
    return this.videoElement;
  }
  
  /**
   * Get the subtitle container element.
   */
  getSubtitleContainer() {
    return this.subtitleContainer;
  }
  
  /**
   * Get current detection method.
   */
  getMethod() {
    return this.currentMethod;
  }
  
  // ============ Private Methods ============
  
  /**
   * Set up HTML5 TextTrack detection.
   */
  _setupTextTrackDetection() {
    const videos = document.querySelectorAll('video');
    
    for (const video of videos) {
      const tracks = video.textTracks;
      
      if (tracks && tracks.length > 0) {
        console.log('[Detector] Found TextTracks:', tracks.length);
        
        for (let i = 0; i < tracks.length; i++) {
          const track = tracks[i];
          
          // Look for subtitle/caption tracks
          if (track.kind === 'subtitles' || track.kind === 'captions') {
            console.log('[Detector] Using TextTrack:', track.label || track.language);
            
            this.videoElement = video;
            this.currentMethod = 'texttrack';
            
            // Set track to showing to get cue events
            if (track.mode === 'disabled') {
              track.mode = 'hidden'; // Enable but don't show native captions
            }
            
            // Listen for cue changes
            track.addEventListener('cuechange', (e) => this._handleCueChange(e));
            
            return true;
          }
        }
      }
    }
    
    return false;
  }
  
  /**
   * Handle TextTrack cue change events.
   */
  _handleCueChange(event) {
    const track = event.target;
    const activeCues = track.activeCues;
    
    if (!activeCues || activeCues.length === 0) {
      // All cues ended
      for (const [cueId, cue] of this.activeCues) {
        if (this.onCueEndCallback) {
          this.onCueEndCallback(cue);
        }
      }
      this.activeCues.clear();
      return;
    }
    
    // Process active cues
    const currentCueIds = new Set();
    
    for (let i = 0; i < activeCues.length; i++) {
      const vttCue = activeCues[i];
      const cueId = this._generateCueId(vttCue.text, vttCue.startTime, vttCue.endTime);
      currentCueIds.add(cueId);
      
      if (!this.activeCues.has(cueId)) {
        // New cue
        const cue = {
          cueId: cueId,
          text: vttCue.text,
          startTime: vttCue.startTime,
          endTime: vttCue.endTime,
          track: track.label || track.language || 'unknown',
        };
        
        this.activeCues.set(cueId, cue);
        
        if (this.onCueCallback) {
          this.onCueCallback(cue);
        }
      }
    }
    
    // Remove ended cues
    for (const [cueId, cue] of this.activeCues) {
      if (!currentCueIds.has(cueId)) {
        this.activeCues.delete(cueId);
        if (this.onCueEndCallback) {
          this.onCueEndCallback(cue);
        }
      }
    }
  }
  
  /**
   * Set up DOM-based subtitle detection.
   */
  _setupDOMDetection() {
    const adapter = this._getAdapter();
    console.log('[Detector] Using DOM detection with adapter:', adapter);

    // Try to find subtitle container using multiple strategies
    this.subtitleContainer = this._findSubtitleContainer(adapter);

    if (!this.subtitleContainer) {
      console.log('[Detector] Subtitle container not found, watching for it...');
      this._watchForContainer(adapter);
      return;
    }

    this.currentMethod = 'dom';
    this._observeSubtitles(adapter.selector);
  }

  /**
   * Find subtitle container using multiple strategies.
   */
  _findSubtitleContainer(adapter) {
    // Strategy 1: Try the specified container selector
    let container = document.querySelector(adapter.container);
    if (container) {
      console.log('[Detector] Found container via primary selector');
      return container;
    }

    // Strategy 2: Try common container patterns
    const commonContainers = [
      '.video-container',
      '.player-container',
      '.video-wrapper',
      '[class*="player"]',
      '[class*="video"]',
      '.dplayer',
      '.plyr',
      '.jw-wrapper',
      '.vjs-text-track-display',
    ];

    for (const sel of commonContainers) {
      container = document.querySelector(sel);
      if (container) {
        console.log('[Detector] Found container via common pattern:', sel);
        return container;
      }
    }

    // Strategy 3: Find container near video element
    const video = document.querySelector('video');
    if (video) {
      // Check video parent elements for subtitle containers
      let parent = video.parentElement;
      for (let i = 0; i < 5 && parent; i++) {
        // Look for subtitle elements within this parent
        const subtitleEl = parent.querySelector('[class*="subtitle"], [class*="caption"]');
        if (subtitleEl) {
          console.log('[Detector] Found container near video element');
          return parent;
        }
        parent = parent.parentElement;
      }
    }

    return null;
  }
  
  /**
   * Watch for subtitle container to appear.
   */
  _watchForContainer(adapter) {
    const observer = new MutationObserver((mutations) => {
      const container = document.querySelector(adapter.container);
      if (container) {
        console.log('[Detector] Subtitle container found');
        this.subtitleContainer = container;
        this.currentMethod = 'dom';
        observer.disconnect();
        this._observeSubtitles(adapter.selector);
      }
    });
    
    observer.observe(document.body, {
      childList: true,
      subtree: true,
    });
  }
  
  /**
   * Observe subtitle elements for changes.
   */
  _observeSubtitles(selector) {
    const target = this.subtitleContainer || document.body;
    
    this.domObserver = new MutationObserver((mutations) => {
      this._checkForSubtitleChanges(selector);
    });
    
    this.domObserver.observe(target, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    
    // Initial check
    this._checkForSubtitleChanges(selector);
  }
  
  /**
   * Check for subtitle text changes.
   */
  _checkForSubtitleChanges(selector) {
    const elements = document.querySelectorAll(selector);
    let currentText = '';
    
    elements.forEach(el => {
      const text = el.textContent?.trim();
      if (text) {
        currentText += (currentText ? '\n' : '') + text;
      }
    });
    
    // Skip if text hasn't changed
    if (currentText === this.lastCueText) {
      return;
    }
    
    // Handle cue end
    if (!currentText && this.lastCueText) {
      for (const [cueId, cue] of this.activeCues) {
        if (this.onCueEndCallback) {
          this.onCueEndCallback(cue);
        }
      }
      this.activeCues.clear();
      this.lastCueText = '';
      return;
    }
    
    // Handle new cue
    if (currentText) {
      const now = this._getVideoTime();
      const cueId = this._generateCueId(currentText, now, now + 5);
      
      const cue = {
        cueId: cueId,
        text: currentText,
        startTime: now,
        endTime: now + 5, // Estimate
        track: 'dom',
      };
      
      // Clear old cues
      for (const [oldId, oldCue] of this.activeCues) {
        if (this.onCueEndCallback) {
          this.onCueEndCallback(oldCue);
        }
      }
      this.activeCues.clear();
      
      // Add new cue
      this.activeCues.set(cueId, cue);
      this.lastCueText = currentText;
      
      if (this.onCueCallback) {
        this.onCueCallback(cue);
      }
    }
  }
  
  /**
   * Set up observer for new video elements.
   */
  _setupVideoObserver() {
    this.videoObserver = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        for (const node of mutation.addedNodes) {
          if (node.nodeName === 'VIDEO') {
            console.log('[Detector] New video element found');
            this._setupTextTrackDetection();
          }
        }
      }
    });
    
    this.videoObserver.observe(document.body, {
      childList: true,
      subtree: true,
    });
  }
  
  /**
   * Get the appropriate adapter for the current site.
   */
  _getAdapter() {
    // Check for manual override first
    if (this.manualSelector) {
      return {
        selector: this.manualSelector,
        container: this.manualContainer || 'body',
        mode: 'dom',
      };
    }

    const hostname = window.location.hostname;

    for (const [site, adapter] of Object.entries(this.siteAdapters)) {
      if (site !== 'default' && hostname.includes(site)) {
        return adapter;
      }
    }

    return this.siteAdapters.default;
  }

  /**
   * Set manual subtitle selector (for sites not in adapter list).
   * @param {string} selector - CSS selector for subtitle text elements
   * @param {string} container - CSS selector for container to observe
   */
  setManualSelector(selector, container = null) {
    console.log('[Detector] Setting manual selector:', selector);
    this.manualSelector = selector;
    this.manualContainer = container;

    // Restart detection with new selector
    if (this.active) {
      this.stop();
      this.start();
    }
  }

  /**
   * Clear manual selector and revert to auto-detection.
   */
  clearManualSelector() {
    this.manualSelector = null;
    this.manualContainer = null;

    if (this.active) {
      this.stop();
      this.start();
    }
  }

  /**
   * Start element picker mode.
   * Allows user to click on subtitle text to set selector.
   * @param {Function} onPick - Callback with picked element's selector
   */
  startElementPicker(onPick) {
    console.log('[Detector] Starting element picker mode');

    // Add highlight styles
    const style = document.createElement('style');
    style.id = 'subtitle-picker-style';
    style.textContent = `
      .subtitle-picker-highlight {
        outline: 3px solid #00d4ff !important;
        outline-offset: 2px !important;
        cursor: crosshair !important;
      }
      .subtitle-picker-overlay {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        z-index: 999998;
        cursor: crosshair;
      }
      .subtitle-picker-tooltip {
        position: fixed;
        top: 10px;
        left: 50%;
        transform: translateX(-50%);
        background: #1a1a2e;
        color: #00d4ff;
        padding: 12px 20px;
        border-radius: 8px;
        font-family: system-ui, sans-serif;
        font-size: 14px;
        z-index: 999999;
        box-shadow: 0 4px 20px rgba(0,0,0,0.5);
      }
    `;
    document.head.appendChild(style);

    // Add tooltip
    const tooltip = document.createElement('div');
    tooltip.className = 'subtitle-picker-tooltip';
    tooltip.textContent = 'Click on a subtitle element to select it. Press ESC to cancel.';
    document.body.appendChild(tooltip);

    let currentHighlight = null;

    const handleMouseOver = (e) => {
      if (currentHighlight) {
        currentHighlight.classList.remove('subtitle-picker-highlight');
      }
      currentHighlight = e.target;
      currentHighlight.classList.add('subtitle-picker-highlight');
    };

    const handleMouseOut = (e) => {
      if (e.target === currentHighlight) {
        currentHighlight.classList.remove('subtitle-picker-highlight');
        currentHighlight = null;
      }
    };

    const handleClick = (e) => {
      e.preventDefault();
      e.stopPropagation();

      const element = e.target;
      const selector = this._generateSelector(element);

      console.log('[Detector] Element picked:', selector);

      cleanup();

      if (onPick) {
        onPick({
          selector: selector,
          element: element,
          text: element.textContent,
        });
      }
    };

    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        cleanup();
        if (onPick) {
          onPick(null);
        }
      }
    };

    const cleanup = () => {
      document.removeEventListener('mouseover', handleMouseOver, true);
      document.removeEventListener('mouseout', handleMouseOut, true);
      document.removeEventListener('click', handleClick, true);
      document.removeEventListener('keydown', handleKeyDown);

      if (currentHighlight) {
        currentHighlight.classList.remove('subtitle-picker-highlight');
      }

      const style = document.getElementById('subtitle-picker-style');
      if (style) style.remove();

      tooltip.remove();
    };

    document.addEventListener('mouseover', handleMouseOver, true);
    document.addEventListener('mouseout', handleMouseOut, true);
    document.addEventListener('click', handleClick, true);
    document.addEventListener('keydown', handleKeyDown);

    return cleanup;
  }

  /**
   * Generate a CSS selector for an element.
   */
  _generateSelector(element) {
    // Try ID first
    if (element.id) {
      return `#${element.id}`;
    }

    // Try class-based selector
    if (element.className && typeof element.className === 'string') {
      const classes = element.className.split(' ').filter(c => c && !c.startsWith('subtitle-picker'));
      if (classes.length > 0) {
        const selector = '.' + classes.join('.');
        // Verify it's unique enough
        if (document.querySelectorAll(selector).length <= 10) {
          return selector;
        }
      }
    }

    // Fall back to tag + parent classes
    let selector = element.tagName.toLowerCase();
    let parent = element.parentElement;

    while (parent && parent !== document.body) {
      if (parent.className && typeof parent.className === 'string') {
        const parentClasses = parent.className.split(' ').filter(c => c);
        if (parentClasses.length > 0) {
          selector = '.' + parentClasses[0] + ' ' + selector;
          if (document.querySelectorAll(selector).length <= 10) {
            return selector;
          }
        }
      }
      parent = parent.parentElement;
    }

    return selector;
  }
  
  /**
   * Get current video playback time.
   */
  _getVideoTime() {
    if (this.videoElement) {
      return this.videoElement.currentTime;
    }
    
    const video = document.querySelector('video');
    return video ? video.currentTime : Date.now() / 1000;
  }
  
  /**
   * Generate a unique cue ID.
   */
  _generateCueId(text, startTime, endTime) {
    // Use a hash of text + timing for stable IDs
    const input = `${text}|${startTime.toFixed(2)}|${endTime.toFixed(2)}`;
    let hash = 0;
    for (let i = 0; i < input.length; i++) {
      const char = input.charCodeAt(i);
      hash = ((hash << 5) - hash) + char;
      hash = hash & hash; // Convert to 32bit integer
    }
    return `cue_${Math.abs(hash).toString(36)}`;
  }
}

// Export singleton instance
window.subtitleDetector = new SubtitleDetector();
