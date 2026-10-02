/**
 * Subtitle Overlay Manager
 *
 * Handles:
 * - Injecting overlay container using Shadow DOM
 * - Displaying translated subtitles above original
 * - Font/style management
 * - User editing of translations
 * - Responsive positioning
 */

class SubtitleOverlay {
  constructor() {
    // State
    this.visible = true;
    this.primaryOnTop = true; // Primary language on top
    this.showOriginal = true;
    this.currentCues = new Map(); // cueId -> { original, translations }

    // Language configuration
    this.primaryLang = 'en';
    this.secondaryLang = 'zh';
    this.targetLanguages = ['en', 'zh'];

    // Settings
    this.settings = {
      fontSize: 20,
      fontFamily: '"Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif',
      opacity: 0.9,
      position: 'above', // 'above' or 'below'
      maxLines: 2,
      // Dynamic colors per language
      primaryColor: '#ffffff',
      secondaryColor: '#ffeb3b',
      // Legacy en/zh colors for backwards compatibility
      enColor: '#ffffff',
      zhColor: '#ffeb3b',
      originalColor: '#aaaaaa',
      bgColor: 'rgba(0, 0, 0, 0.75)',
      padding: '8px 16px',
      borderRadius: '4px',
      lineHeight: 1.4,
    };

    // DOM elements
    this.hostElement = null;
    this.shadowRoot = null;
    this.container = null;
    this.subtitleStack = null;

    // Edit state
    this.editMode = false;
    this.editCueId = null;
    this.onCorrectionCallback = null;

    // Position tracking
    this.videoElement = null;
    this.resizeObserver = null;

    // Live-caption state
    this.partial = null;   // { cueId, text, lang } - unstable ASR hypothesis
    this.notice = null;    // { text, kind } - short status line (warnings, detected language)
    this._noticeTimer = null;
  }

  /**
   * Initialize the overlay.
   */
  init() {
    if (this.hostElement) {
      console.log('[Overlay] Already initialized');
      return;
    }

    console.log('[Overlay] Initializing');
    this._createOverlay();
    this._setupResizeObserver();
    this._injectStyles();
  }

  /**
   * Destroy the overlay.
   */
  destroy() {
    if (this.resizeObserver) {
      this.resizeObserver.disconnect();
    }

    if (this.hostElement) {
      this.hostElement.remove();
    }

    this.hostElement = null;
    this.shadowRoot = null;
    this.container = null;
    this.subtitleStack = null;
    this.currentCues.clear();
  }

  /**
   * Show a translation for a cue.
   */
  showTranslation(cueId, originalText, translations, options = {}) {
    if (!this.container) {
      this.init();
    }

    const existing = this.currentCues.get(cueId);
    // Store cue data (merge so a later revision keeps original/sourceLang)
    this.currentCues.set(cueId, {
      original: originalText,
      translations: translations || {},
      sourceLang: options.sourceLang || (existing && existing.sourceLang) || null,
      revised: !!options.revised,
    });

    // A final caption replaces any partial for the same cue
    if (this.partial && this.partial.cueId === cueId) {
      this.partial = null;
    }

    // Update display
    this._updateDisplay();
  }

  /**
   * Show an unstable (partial) live caption. Rendered dimmed, no animation.
   */
  showPartial(cueId, text, lang = null) {
    if (!this.container) {
      this.init();
    }
    this.partial = { cueId, text, lang };
    this._updateDisplay();
  }

  clearPartial(cueId = null) {
    if (this.partial && (cueId === null || this.partial.cueId === cueId)) {
      this.partial = null;
      this._updateDisplay();
    }
  }

  /**
   * Show a short status notice (e.g. "Listening (Bengali)", "No audio").
   */
  showNotice(text, kind = 'info', ttlMs = 4000) {
    if (!this.container) {
      this.init();
    }
    this.notice = text ? { text, kind } : null;
    if (this._noticeTimer) clearTimeout(this._noticeTimer);
    if (text && ttlMs > 0) {
      this._noticeTimer = setTimeout(() => { this.notice = null; this._updateDisplay(); }, ttlMs);
    }
    this._updateDisplay();
  }

  /**
   * Hide a cue's translation.
   */
  hideTranslation(cueId) {
    this.currentCues.delete(cueId);
    this._updateDisplay();
  }

  /**
   * Clear all translations.
   */
  clear() {
    this.currentCues.clear();
    this.partial = null;
    this._updateDisplay();
  }

  /**
   * Toggle overlay visibility.
   */
  toggle() {
    this.visible = !this.visible;
    if (this.container) {
      this.container.style.display = this.visible ? 'flex' : 'none';
    }
    console.log('[Overlay] Visibility:', this.visible);
    return this.visible;
  }

  /**
   * Show the overlay.
   */
  show() {
    this.visible = true;
    if (this.container) {
      this.container.style.display = 'flex';
    }
  }

  /**
   * Hide the overlay.
   */
  hide() {
    this.visible = false;
    if (this.container) {
      this.container.style.display = 'none';
    }
  }

  /**
   * Swap primary/secondary language order.
   */
  swapOrder() {
    this.primaryOnTop = !this.primaryOnTop;
    this._updateDisplay();
    console.log('[Overlay] Primary on top:', this.primaryOnTop);
    return this.primaryOnTop;
  }

  /**
   * Increase font size.
   */
  increaseFontSize(amount = 2) {
    this.settings.fontSize = Math.min(40, this.settings.fontSize + amount);
    this._updateStyles();
    console.log('[Overlay] Font size:', this.settings.fontSize);
    return this.settings.fontSize;
  }

  /**
   * Decrease font size.
   */
  decreaseFontSize(amount = 2) {
    this.settings.fontSize = Math.max(12, this.settings.fontSize - amount);
    this._updateStyles();
    console.log('[Overlay] Font size:', this.settings.fontSize);
    return this.settings.fontSize;
  }

  /**
   * Update settings including language configuration.
   */
  updateSettings(newSettings) {
    Object.assign(this.settings, newSettings);

    // Update language configuration if provided
    if (newSettings.primaryLang) {
      this.primaryLang = newSettings.primaryLang;
    }
    if (newSettings.secondaryLang) {
      this.secondaryLang = newSettings.secondaryLang;
    }
    if (newSettings.targetLanguages) {
      this.targetLanguages = newSettings.targetLanguages;
    }

    this._updateStyles();
    this._updateDisplay();
  }

  /**
   * Enable edit mode for a cue.
   */
  enableEditMode(cueId) {
    this.editMode = true;
    this.editCueId = cueId;
    this._updateDisplay();
  }

  /**
   * Disable edit mode.
   */
  disableEditMode() {
    this.editMode = false;
    this.editCueId = null;
    this._updateDisplay();
  }

  /**
   * Set callback for corrections.
   */
  onCorrection(callback) {
    this.onCorrectionCallback = callback;
  }

  /**
   * Set the video element for positioning.
   */
  setVideoElement(video) {
    this.videoElement = video;
    this._updatePosition();
  }

  // ============ Private Methods ============

  /**
   * Create the overlay DOM structure.
   */
  _createOverlay() {
    // Create host element
    this.hostElement = document.createElement('div');
    this.hostElement.id = 'subtitle-translator-host';
    this.hostElement.style.cssText = `
      position: fixed;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      pointer-events: none;
      z-index: 2147483647;
    `;

    // Attach shadow DOM
    this.shadowRoot = this.hostElement.attachShadow({ mode: 'closed' });

    // Create container
    this.container = document.createElement('div');
    this.container.className = 'overlay-container';

    // Create subtitle stack
    this.subtitleStack = document.createElement('div');
    this.subtitleStack.className = 'subtitle-stack';

    this.container.appendChild(this.subtitleStack);
    this.shadowRoot.appendChild(this.container);

    // Add to page
    document.body.appendChild(this.hostElement);
  }

  /**
   * Inject styles into shadow DOM.
   */
  _injectStyles() {
    const style = document.createElement('style');
    style.textContent = this._getStyles();
    this.shadowRoot.appendChild(style);
  }

  /**
   * Get CSS styles.
   */
  _getStyles() {
    return `
      .overlay-container {
        position: fixed;
        bottom: 15%;
        left: 50%;
        transform: translateX(-50%);
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 4px;
        max-width: 80%;
        pointer-events: none;
      }

      .subtitle-stack {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 2px;
      }

      .subtitle-line {
        font-family: ${this.settings.fontFamily};
        font-size: ${this.settings.fontSize}px;
        line-height: ${this.settings.lineHeight};
        padding: ${this.settings.padding};
        background: ${this.settings.bgColor};
        border-radius: ${this.settings.borderRadius};
        text-align: center;
        white-space: pre-wrap;
        word-wrap: break-word;
        max-width: 100%;
        text-shadow: 1px 1px 2px rgba(0,0,0,0.8),
                     -1px -1px 2px rgba(0,0,0,0.8);
        pointer-events: auto;
      }

      .subtitle-line.primary {
        color: ${this.settings.primaryColor || this.settings.enColor};
      }

      .subtitle-line.secondary {
        color: ${this.settings.secondaryColor || this.settings.zhColor};
      }

      /* Legacy en/zh classes for backwards compatibility */
      .subtitle-line.en {
        color: ${this.settings.enColor};
      }

      .subtitle-line.zh {
        color: ${this.settings.zhColor};
      }

      .subtitle-line.original {
        color: ${this.settings.originalColor};
        font-size: ${this.settings.fontSize * 0.85}px;
        opacity: 0.8;
      }

      .subtitle-line.partial {
        color: #c8c8c8;
        opacity: 0.75;
        font-style: italic;
        animation: none;
      }

      .subtitle-line.revised {
        animation: none;
      }

      .subtitle-line.notice {
        font-size: ${Math.max(11, this.settings.fontSize * 0.6)}px;
        color: #9be7ff;
        background: rgba(0, 0, 0, 0.55);
        opacity: 0.9;
      }

      .subtitle-line.notice.warn {
        color: #ffb74d;
      }

      .subtitle-line.editing {
        background: rgba(33, 150, 243, 0.9);
        cursor: text;
      }

      .subtitle-line[contenteditable="true"]:focus {
        outline: 2px solid #2196f3;
        outline-offset: 2px;
      }

      .edit-hint {
        font-size: 12px;
        color: #888;
        margin-top: 4px;
      }

      @keyframes fadeIn {
        from { opacity: 0; transform: translateY(10px); }
        to { opacity: 1; transform: translateY(0); }
      }

      .subtitle-line {
        animation: fadeIn 0.2s ease-out;
      }
    `;
  }

  /**
   * Update inline styles.
   */
  _updateStyles() {
    const style = this.shadowRoot.querySelector('style');
    if (style) {
      style.textContent = this._getStyles();
    }
  }

  /**
   * Update the display with current cues.
   * Now supports arbitrary language pairs (not just en/zh).
   */
  _updateDisplay() {
    if (!this.subtitleStack) return;

    // Clear existing
    this.subtitleStack.innerHTML = '';

    if (this.currentCues.size === 0) {
      this._renderExtras();
      return;
    }

    // Get the most recent cue
    const lastCue = Array.from(this.currentCues.entries()).pop();
    if (!lastCue) { this._renderExtras(); return; }

    const [cueId, cueData] = lastCue;
    const { original, translations } = cueData;
    const revised = !!cueData.revised;

    // Determine order based on primaryOnTop and configured languages
    const primary = this.primaryOnTop ? this.primaryLang : this.secondaryLang;
    const secondary = this.primaryOnTop ? this.secondaryLang : this.primaryLang;

    // Add primary translation
    if (translations[primary]) {
      const line = this._createSubtitleLine(
        translations[primary].display_text || translations[primary].single_line,
        'primary',
        cueId,
        primary,
        revised
      );
      this.subtitleStack.appendChild(line);
    }

    // Add secondary translation (if configured and different from primary)
    if (secondary && secondary !== 'none' && translations[secondary]) {
      const line = this._createSubtitleLine(
        translations[secondary].display_text || translations[secondary].single_line,
        'secondary',
        cueId,
        secondary,
        revised
      );
      this.subtitleStack.appendChild(line);
    }

    // Add original if enabled (always for live captions that have no translation yet)
    const hasTranslation = Object.keys(translations || {}).length > 0;
    if ((this.showOriginal || !hasTranslation) && original) {
      const line = this._createSubtitleLine(original, 'original', cueId, null, revised);
      this.subtitleStack.appendChild(line);
    }

    this._renderExtras();
  }

  /**
   * Render the partial (unstable) caption and any notice below the stack.
   */
  _renderExtras() {
    if (this.partial && this.partial.text) {
      const line = document.createElement('div');
      line.className = 'subtitle-line partial';
      line.textContent = this.partial.text;
      line.dataset.cueId = this.partial.cueId;
      this.subtitleStack.appendChild(line);
    }
    if (this.notice && this.notice.text) {
      const line = document.createElement('div');
      line.className = 'subtitle-line notice' + (this.notice.kind === 'warn' ? ' warn' : '');
      line.textContent = this.notice.text;
      this.subtitleStack.appendChild(line);
    }
  }

  /**
   * Create a subtitle line element.
   * @param {string} text - The text to display
   * @param {string} type - 'primary', 'secondary', or 'original'
   * @param {string} cueId - The cue ID
   * @param {string} langCode - Optional language code for styling
   */
  _createSubtitleLine(text, type, cueId, langCode = null, revised = false) {
    const line = document.createElement('div');
    line.className = `subtitle-line ${type}` + (revised ? ' revised' : '');
    line.textContent = text;
    line.dataset.cueId = cueId;
    line.dataset.type = type;
    if (langCode) {
      line.dataset.lang = langCode;
    }

    // Handle edit mode
    if (this.editMode && this.editCueId === cueId && type !== 'original') {
      line.classList.add('editing');
      line.contentEditable = 'true';
      line.spellcheck = false;

      // Save on blur or Enter
      line.addEventListener('blur', () => this._handleEdit(line, cueId, type));
      line.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          line.blur();
        }
        if (e.key === 'Escape') {
          this.disableEditMode();
        }
      });

      // Focus after render
      setTimeout(() => line.focus(), 0);
    } else {
      // Double-click to edit
      line.addEventListener('dblclick', () => {
        if (type !== 'original') {
          this.enableEditMode(cueId);
        }
      });
    }

    return line;
  }

  /**
   * Handle edit completion.
   */
  _handleEdit(line, cueId, type) {
    const newText = line.textContent.trim();
    const cueData = this.currentCues.get(cueId);

    if (!cueData) {
      this.disableEditMode();
      return;
    }

    const oldText = cueData.translations[type]?.single_line;

    if (newText !== oldText && this.onCorrectionCallback) {
      // Build correction data
      const originalTranslation = {};
      const correctedTranslation = {};

      const langs = Object.keys(cueData.translations || {});
      for (const lang of langs) {
        originalTranslation[lang] = cueData.translations[lang]?.single_line || '';
        correctedTranslation[lang] = lang === type ? newText : originalTranslation[lang];
      }

      this.onCorrectionCallback({
        cueId: cueId,
        sourceText: cueData.original,
        sourceLang: cueData.sourceLang || null,
        originalTranslation: originalTranslation,
        correctedTranslation: correctedTranslation,
      });

      // Update local display
      if (cueData.translations[type]) {
        cueData.translations[type].single_line = newText;
        cueData.translations[type].display_text = newText;
      }
    }

    this.disableEditMode();
  }

  /**
   * Set up resize observer for responsive positioning.
   */
  _setupResizeObserver() {
    this.resizeObserver = new ResizeObserver(() => {
      this._updatePosition();
    });

    this.resizeObserver.observe(document.body);
  }

  /**
   * Update overlay position based on video element.
   */
  _updatePosition() {
    if (!this.container) return;

    if (this.videoElement) {
      const rect = this.videoElement.getBoundingClientRect();
      const bottomOffset = window.innerHeight - rect.bottom + 50;

      this.container.style.bottom = `${Math.max(50, bottomOffset)}px`;
      this.container.style.maxWidth = `${rect.width * 0.9}px`;
    }
  }
}

// Export singleton instance
window.subtitleOverlay = new SubtitleOverlay();
