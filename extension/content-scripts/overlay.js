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
    this.partial = null;   // { cueId, text, lang, provisional } - unstable ASR hypothesis
    this.notice = null;    // { text, kind } - short status line (warnings, detected language)
    this._noticeTimer = null;

    // Live captions with auto-detect: the backend's language status for the session
    // (provisional | confirmed | manual | fallback | error; null = no live session).
    // Only confirmed and manual are certain; until then live text is drawn dimmed.
    this.langStatus = null;
    this.liveLang = null;        // language of the recognizer the status is about
    this.liveLangName = null;
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
   * Show a translation for a cue. Returns false when it was not drawn (a live caption
   * from a recognizer that has been replaced since), else true.
   */
  showTranslation(cueId, originalText, translations, options = {}) {
    if (!this.container) {
      this.init();
    }

    const existing = this.currentCues.get(cueId);
    const drawn = options.langStatus ? this._treatment(options.langStatus, options.sourceLang)
      : (existing && existing.provisional ? 'dim' : 'normal');
    if (drawn === 'drop') return false;
    // Store cue data (merge so a later revision keeps original/sourceLang)
    this.currentCues.set(cueId, {
      original: originalText,
      translations: translations || {},
      sourceLang: options.sourceLang || (existing && existing.sourceLang) || null,
      revised: !!options.revised,
      provisional: drawn === 'dim',
    });

    // A final caption replaces any partial for the same cue
    if (this.partial && this.partial.cueId === cueId) {
      this.partial = null;
    }

    // Update display
    this._updateDisplay();
    return true;
  }

  /**
   * Show an unstable (partial) live caption. Rendered dimmed, no animation.
   * langStatus: the backend's lang_status of the caption (live captions only).
   */
  showPartial(cueId, text, lang = null, langStatus = null) {
    if (!this.container) {
      this.init();
    }
    const drawn = langStatus ? this._treatment(langStatus, lang) : 'normal';
    if (drawn === 'drop') return false;
    this.partial = { cueId, text, lang, provisional: drawn === 'dim' };
    this._updateDisplay();
    return true;
  }

  /**
   * Live captions: the session's language status, from the backend's 'lid' message
   * (and repeated on every partial / final). While it is not certain, live text is
   * dimmed above a label. When it becomes certain, text from the same recognizer is
   * undimmed in place; text from another recognizer is removed.
   * info: { lang, name } of the recognizer's language.
   */
  setLanguageStatus(status, info = {}) {
    const lang = info.lang || null;
    if (status === this.langStatus && lang === this.liveLang) return;
    const sameRecognizer = !lang || !this.liveLang || lang === this.liveLang;
    this.langStatus = status || null;
    this.liveLang = status ? lang : null;
    this.liveLangName = status ? (info.name || lang) : null;
    if (this._certain(status) || !status) {
      // certain now: the same recognizer's text is undimmed in place; another one's text,
      // or dimmed text left when the session ends, goes
      const keep = !!status && sameRecognizer;
      for (const [id, cue] of Array.from(this.currentCues.entries())) {
        if (!cue.provisional) continue;
        if (keep) cue.provisional = false; else this.currentCues.delete(id);
      }
      if (this.partial && this.partial.provisional) {
        if (keep) this.partial.provisional = false; else this.partial = null;
      }
    }
    this._updateDisplay();
  }

  /**
   * Detection switched the language (backend 'reset'): what the provisional recognizer
   * wrote is removed. Whatever of it is still on its way (a translation of one of its
   * sentences) is dropped when it arrives, see _treatment.
   */
  discardProvisional() {
    this.clear();
  }

  _certain(status) {
    return status === 'confirmed' || status === 'manual';
  }

  /**
   * How a live caption with the backend's lang_status (and language) is drawn: 'dim'
   * while the language is not certain, 'normal' once it is, 'drop' when the caption was
   * written by the recognizer that has been replaced since.
   */
  _treatment(langStatus, lang = null) {
    if (this._certain(langStatus)) return 'normal';
    if (this._certain(this.langStatus)) {
      // Written before the language was certain: by the recognizer that is still running
      // (detection agreed with it), or by the one detection replaced. The replaced one's
      // utterance ids start again in the new one, so its late translations must not land.
      return lang && this.liveLang && lang !== this.liveLang ? 'drop' : 'normal';
    }
    if (!this.langStatus) {  // script injected mid-session: the caption itself says the status
      this.langStatus = langStatus;
      this.liveLang = lang;
      this.liveLangName = lang;
    }
    return 'dim';
  }

  /** The label under dimmed text: what the overlay is waiting for. */
  _languageLabel() {
    if (this.langStatus === 'provisional') return { text: 'Detecting language…', kind: 'info' };
    if (this.langStatus === 'fallback' || this.langStatus === 'error') {
      const assumed = this.liveLangName ? `, assuming ${this.liveLangName}` : '';
      return { text: `Language not detected${assumed}. Pick it in the popup.`, kind: 'warn' };
    }
    return null;
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
    this._editingLine = null;
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

      /* the mark of a line in progress; drawn by the style sheet so the text stays the recognizer's */
      .subtitle-line.in-progress::after {
        content: ' …';
      }

      .subtitle-line.revised {
        animation: none;
      }

      /* live text from a recognizer whose language is not confirmed yet
         (after .partial and .original: same specificity, the later rule wins) */
      .subtitle-line.provisional {
        opacity: 0.45;
        animation: none;
      }

      .subtitle-line.notice {
        font-size: ${Math.max(11, this.settings.fontSize * 0.6)}px;
        color: #9be7ff;
        background: rgba(0, 0, 0, 0.55);
        opacity: 0.9;
        animation: none;
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
    // While the user is typing a correction, new captions must not rebuild (and so
    // discard) the line being edited; the display catches up when the edit ends.
    if (this.editMode && this._editingLine && this._editingLine.isConnected) return;

    // Clear existing
    this.subtitleStack.innerHTML = '';

    if (this.currentCues.size === 0) {
      this._renderExtras();
      return;
    }

    // The cue being edited, else the most recent cue
    const lastCue = this.editMode && this.currentCues.has(this.editCueId)
      ? [this.editCueId, this.currentCues.get(this.editCueId)]
      : Array.from(this.currentCues.entries()).pop();
    if (!lastCue) { this._renderExtras(); return; }

    const [cueId, cueData] = lastCue;
    const { original, translations } = cueData;
    const revised = !!cueData.revised;
    const dim = (line) => { if (cueData.provisional) line.classList.add('provisional'); return line; };

    // Determine order based on primaryOnTop and configured languages
    const primary = this.primaryOnTop ? this.primaryLang : this.secondaryLang;
    const secondary = this.primaryOnTop ? this.secondaryLang : this.primaryLang;

    // Only ok / fallback targets are translations; untranslated (source placeholder)
    // and error targets are not drawn as a language line.
    const usable = (lang) => globalThis.STWsProtocol ? globalThis.STWsProtocol.usableTranslation(translations[lang]) : !!translations[lang];

    // Add primary translation
    if (usable(primary)) {
      const line = this._createSubtitleLine(
        translations[primary].display_text || translations[primary].single_line,
        'primary',
        cueId,
        primary,
        revised
      );
      this.subtitleStack.appendChild(dim(line));
    }

    // Add secondary translation (if configured and different from primary)
    if (secondary && secondary !== 'none' && usable(secondary)) {
      const line = this._createSubtitleLine(
        translations[secondary].display_text || translations[secondary].single_line,
        'secondary',
        cueId,
        secondary,
        revised
      );
      this.subtitleStack.appendChild(dim(line));
    }

    // Add original if enabled (always for live captions that have no translation yet)
    const hasTranslation = Object.keys(translations || {}).some(usable);
    if ((this.showOriginal || !hasTranslation) && original) {
      const line = this._createSubtitleLine(original, 'original', cueId, null, revised);
      this.subtitleStack.appendChild(dim(line));
    }

    this._renderExtras();
  }

  /**
   * Render the partial (unstable) caption, the language label and any notice below the stack.
   */
  _renderExtras() {
    if (this.partial && this.partial.text) {
      const line = document.createElement('div');
      // the line still being recognised: its text grows with every partial result
      line.className = 'subtitle-line partial in-progress' + (this.partial.provisional ? ' provisional' : '');
      line.textContent = this.partial.text;
      line.dataset.cueId = this.partial.cueId;
      line.dataset.state = 'in-progress';
      this.subtitleStack.appendChild(line);
    }
    const label = this._languageLabel();
    if (label) {
      const line = document.createElement('div');
      line.className = 'subtitle-line notice lang-pending' + (label.kind === 'warn' ? ' warn' : '');
      line.textContent = label.text;
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
      this._editingLine = line;
      line.contentEditable = 'true';
      line.spellcheck = false;

      // Save on blur or Enter
      // the correction is keyed by the line's language code, not its role (primary / secondary)
      line.addEventListener('blur', () => this._handleEdit(line, cueId, langCode || type));
      line.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          line.blur();
        }
        if (e.key === 'Escape') {
          this.disableEditMode();
        }
      });
      // What is typed here is text, not the page's keyboard shortcuts: a video site's player
      // listens on the document (YouTube: m mutes, j rewinds, space pauses) and would act on
      // every letter of the correction.
      for (const type of ['keydown', 'keypress', 'keyup']) {
        line.addEventListener(type, (e) => e.stopPropagation());
      }

      // Focus after render
      setTimeout(() => line.focus(), 0);
    } else {
      // Double-click to edit
      line.addEventListener('dblclick', () => {
        const cue = this.currentCues.get(cueId);
        if (type !== 'original' && !(cue && cue.provisional)) {
          this.enableEditMode(cueId);
        }
      });
    }

    return line;
  }

  /**
   * Handle edit completion.
   */
  _handleEdit(line, cueId, lang) {
    const newText = line.textContent.trim();
    const cueData = this.currentCues.get(cueId);

    if (!cueData) {
      this.disableEditMode();
      return;
    }

    const oldText = cueData.translations[lang]?.single_line;

    if (newText !== oldText && this.onCorrectionCallback) {
      // Build correction data
      const originalTranslation = {};
      const correctedTranslation = {};

      for (const l of Object.keys(cueData.translations || {})) {
        originalTranslation[l] = cueData.translations[l]?.single_line || '';
        correctedTranslation[l] = l === lang ? newText : originalTranslation[l];
      }

      this.onCorrectionCallback({
        cueId: cueId,
        sourceText: cueData.original,
        sourceLang: cueData.sourceLang || null,
        originalTranslation: originalTranslation,
        correctedTranslation: correctedTranslation,
      });

      // Update local display
      if (cueData.translations[lang]) {
        cueData.translations[lang].single_line = newText;
        cueData.translations[lang].display_text = newText;
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
