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
    // The source line (the spoken words) is off by default: translation only. The popup
    // toggle and Alt+O turn it on; the choice is remembered (settings.showOriginal).
    this.showOriginal = false;
    this.currentCues = new Map(); // cueId -> { original, translations }

    // Language configuration
    this.primaryLang = 'en';
    this.secondaryLang = 'zh';
    this.targetLanguages = ['en', 'zh'];

    // Settings (docs/overlay/README.md has the layout rules)
    this.settings = {
      fontSize: 20,            // px, used only while no video element is known
      fontScalePct: 4.5,       // font size as a percentage of the video's height
      fontMinPx: 14,           // never smaller than this (small embeds)
      sourceScale: 0.8,        // the source line's font relative to the translation's
      sourceOpacity: 0.7,      // ...and its opacity
      fontFamily: '"Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif',
      overlayBgOpacity: 0.6,   // the rounded box behind each line
      position: 'above', // 'above' or 'below'
      maxLines: 2,             // rows per line of text; past it the oldest words go
      maxCharsLatin: 42,       // characters per row
      maxCharsCJK: 22,         // ...for zh / ja / ko
      // Dynamic colors per language
      primaryColor: '#ffffff',
      secondaryColor: '#ffeb3b',
      // Legacy en/zh colors for backwards compatibility
      enColor: '#ffffff',
      zhColor: '#ffeb3b',
      originalColor: '#dddddd',
      borderRadius: '0.35em',
      lineHeight: 1.25,
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
    // Draft translations of lines still open (the backend translates their stable prefix):
    // cueId -> { translations: {lang: t}, provisional }. Drawn until the line's final
    // translation in that language arrives.
    this.drafts = new Map();
    this._openDraftCue = null;
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
    // ...and a final translation replaces the draft in its language
    const draft = this.drafts.get(cueId);
    if (draft) {
      for (const lang of Object.keys(draft.translations)) {
        if (this._usable((translations || {})[lang])) delete draft.translations[lang];
      }
      if (!Object.keys(draft.translations).length) this.drafts.delete(cueId);
    }
    if (this._openDraftCue === cueId) this._openDraftCue = null;

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
   * Draft translation of a live line that is still being recognised (backend 'draft':
   * the translation of the line's stable prefix). Drawn above the growing source text,
   * marked as a draft; a newer draft replaces it, the final translation ends it.
   * Returns false when nothing was drawn: a draft from a replaced recognizer, a target
   * that already has its final translation, or an unusable target.
   */
  showDraft(cueId, translations, options = {}) {
    if (!this.container) {
      this.init();
    }
    const drawn = options.langStatus ? this._treatment(options.langStatus, options.sourceLang) : 'normal';
    if (drawn === 'drop') return false;
    const cue = this.currentCues.get(cueId);
    const fresh = {};
    for (const [lang, t] of Object.entries(translations || {})) {
      if (!this._usable(t)) continue;
      if (cue && this._usable(cue.translations[lang])) continue;  // the final translation is there
      fresh[lang] = t;
    }
    if (!Object.keys(fresh).length) return false;
    const existing = this.drafts.get(cueId);
    this.drafts.delete(cueId);
    this.drafts.set(cueId, { translations: Object.assign({}, existing ? existing.translations : {}, fresh), provisional: drawn === 'dim' });
    while (this.drafts.size > 4) this.drafts.delete(this.drafts.keys().next().value);
    if (!cue) this._openDraftCue = cueId;
    this._updateDisplay();
    return true;
  }

  // Only ok / fallback targets are translations; untranslated (source placeholder) and
  // error targets are not drawn as a language line.
  _usable(t) {
    return globalThis.STWsProtocol ? globalThis.STWsProtocol.usableTranslation(t) : !!t;
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
      for (const [id, d] of Array.from(this.drafts.entries())) {
        if (!d.provisional) continue;
        if (keep) d.provisional = false; else this.drafts.delete(id);
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
    this.drafts.delete(cueId);
    this._updateDisplay();
  }

  /**
   * Clear all translations.
   */
  clear() {
    this.currentCues.clear();
    this.partial = null;
    this.drafts.clear();
    this._openDraftCue = null;
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
   * Increase font size: the percentage of the video's height (and the px fallback).
   * Returns the new settings for the caller to remember.
   */
  increaseFontSize(amount = 0.5) {
    this.settings.fontScalePct = Math.min(10, Math.round((this.settings.fontScalePct + amount) * 10) / 10);
    this.settings.fontSize = Math.min(40, this.settings.fontSize + 2);
    this._updateStyles();
    console.log('[Overlay] Font size:', this.settings.fontScalePct, '% of the video height');
    return { fontScalePct: this.settings.fontScalePct, fontSize: this.settings.fontSize };
  }

  /**
   * Decrease font size.
   */
  decreaseFontSize(amount = 0.5) {
    this.settings.fontScalePct = Math.max(2, Math.round((this.settings.fontScalePct - amount) * 10) / 10);
    this.settings.fontSize = Math.max(12, this.settings.fontSize - 2);
    this._updateStyles();
    console.log('[Overlay] Font size:', this.settings.fontScalePct, '% of the video height');
    return { fontScalePct: this.settings.fontScalePct, fontSize: this.settings.fontSize };
  }

  /**
   * Show or hide the source line (the spoken words under the translation).
   */
  setShowOriginal(on) {
    on = !!on;
    if (on === this.showOriginal) return on;
    this.showOriginal = on;
    this._updateDisplay();
    return on;
  }

  /**
   * How a role is drawn: {fontPx, opacity}. The translation's font follows the video's
   * height (fontScalePct, at least fontMinPx; the fontSize setting while no video is
   * known); the source line (original, partial) is smaller and dimmer.
   */
  roleStyle(role) {
    const layout = globalThis.STOverlayLayout;
    const h = this._videoHeight();
    const base = layout ? layout.fontPx(h, { scalePct: this.settings.fontScalePct, minPx: this.settings.fontMinPx, fallbackPx: this.settings.fontSize })
      : (h > 0 ? Math.max(this.settings.fontMinPx, h * this.settings.fontScalePct / 100) : this.settings.fontSize);
    if (role === 'original' || role === 'partial') {
      return { fontPx: Math.max(this.settings.fontMinPx, Math.round(base * this.settings.sourceScale * 10) / 10), opacity: this.settings.sourceOpacity };
    }
    if (role === 'notice') return { fontPx: Math.max(11, Math.round(base * 0.6 * 10) / 10), opacity: 0.9 };
    return { fontPx: base, opacity: 1 };
  }

  _videoHeight() {
    if (!this.videoElement || typeof this.videoElement.getBoundingClientRect !== 'function') return 0;
    try { return this.videoElement.getBoundingClientRect().height || 0; } catch (_) { return 0; }
  }

  /**
   * The rows a text is drawn on: at most maxLines rows of maxChars (CJK: maxCharsCJK)
   * characters; a longer text keeps its newest words, with an ellipsis at the start.
   */
  _fit(text, lang = null) {
    const layout = globalThis.STOverlayLayout;
    const t = String(text == null ? '' : text).replace(/\s*\n\s*/g, ' ');
    if (!layout) return t;
    const cjk = layout.isCJK(lang);
    const r = layout.fitLines(t, { maxLines: this.settings.maxLines || 2, cjk,
      maxChars: cjk ? (this.settings.maxCharsCJK || 22) : (this.settings.maxCharsLatin || 42) });
    return r.lines.join('\n');
  }

  _translationText(translation) {
    return translation.single_line || (translation.display_text || '').replace(/\n/g, ' ') || '';
  }

  /**
   * Update settings including language configuration.
   */
  updateSettings(newSettings) {
    Object.assign(this.settings, newSettings);
    if ('showOriginal' in newSettings) this.showOriginal = !!newSettings.showOriginal;

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
    if (video === this.videoElement) return;
    this.videoElement = video;
    this._updateStyles();   // the font follows the video's height
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
    const text = this.roleStyle('primary');
    const source = this.roleStyle('original');
    const notice = this.roleStyle('notice');
    const bg = `rgba(0, 0, 0, ${this.settings.overlayBgOpacity})`;
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
        gap: 0.15em;
        font-size: ${text.fontPx}px;
      }

      /* a rounded box behind the text only, not a bar across the picture */
      .subtitle-line {
        display: inline-block;
        width: fit-content;
        max-width: 100%;
        box-sizing: border-box;
        font-family: ${this.settings.fontFamily};
        font-size: ${text.fontPx}px;
        line-height: ${this.settings.lineHeight};
        padding: 0.15em 0.5em;
        background: ${bg};
        border-radius: ${this.settings.borderRadius};
        text-align: center;
        white-space: pre-wrap;
        overflow-wrap: break-word;
        text-shadow: 0 0 2px rgba(0,0,0,0.9);
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

      /* the source line (the spoken words): smaller and dimmer than the translation */
      .subtitle-line.original {
        color: ${this.settings.originalColor};
        font-size: ${source.fontPx}px;
        opacity: ${source.opacity};
      }

      .subtitle-line.partial {
        color: ${this.settings.originalColor};
        font-size: ${source.fontPx}px;
        opacity: ${source.opacity};
        font-style: italic;
        animation: none;
      }

      /* the translation of the part of an open line that has stopped changing; replaced at the endpoint */
      .subtitle-line.draft {
        font-style: italic;
        opacity: 0.8;
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
        font-size: ${notice.fontPx}px;
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

    const usable = (lang) => this._usable(translations[lang]);
    // a live line whose final translation is still on its way keeps its draft until then
    const draft = this.drafts.get(cueId);

    // Add primary translation
    if (usable(primary)) {
      const line = this._createSubtitleLine(this._translationText(translations[primary]), 'primary', cueId, primary, revised);
      this.subtitleStack.appendChild(dim(line));
    } else if (draft && this._usable(draft.translations[primary])) {
      this.subtitleStack.appendChild(this._createDraftLine(draft.translations[primary], 'primary', cueId, primary, draft.provisional || cueData.provisional));
    }

    // Add secondary translation (if configured and different from primary)
    if (secondary && secondary !== 'none' && usable(secondary)) {
      const line = this._createSubtitleLine(this._translationText(translations[secondary]), 'secondary', cueId, secondary, revised);
      this.subtitleStack.appendChild(dim(line));
    } else if (secondary && secondary !== 'none' && draft && this._usable(draft.translations[secondary])) {
      this.subtitleStack.appendChild(this._createDraftLine(draft.translations[secondary], 'secondary', cueId, secondary, draft.provisional || cueData.provisional));
    }

    // The source line when it is on; also when the cue has nothing else to show (no
    // translation yet and no draft), so the viewer is never left with nothing.
    const hasTranslation = Object.keys(translations || {}).some(usable);
    const hasDraft = !!draft && Object.keys(draft.translations).some((l) => this._usable(draft.translations[l]));
    if ((this.showOriginal || !(hasTranslation || hasDraft)) && original) {
      const line = this._createSubtitleLine(original, 'original', cueId, null, revised, cueData.sourceLang);
      this.subtitleStack.appendChild(dim(line));
    }

    this._renderExtras();
  }

  /**
   * Render the partial (unstable) caption, the language label and any notice below the stack.
   */
  _renderExtras() {
    // the open line: its draft translation(s), then its growing source text
    const openCue = this.partial ? this.partial.cueId : this._openDraftCue;
    const draft = openCue && !this.currentCues.has(openCue) ? this.drafts.get(openCue) : null;
    if (draft) {
      const primary = this.primaryOnTop ? this.primaryLang : this.secondaryLang;
      const secondary = this.primaryOnTop ? this.secondaryLang : this.primaryLang;
      const dimmed = draft.provisional || !!(this.partial && this.partial.provisional);
      if (this._usable(draft.translations[primary])) {
        this.subtitleStack.appendChild(this._createDraftLine(draft.translations[primary], 'primary', openCue, primary, dimmed));
      }
      if (secondary && secondary !== 'none' && this._usable(draft.translations[secondary])) {
        this.subtitleStack.appendChild(this._createDraftLine(draft.translations[secondary], 'secondary', openCue, secondary, dimmed));
      }
    }
    if (this.showOriginal && this.partial && this.partial.text) {
      const line = document.createElement('div');
      // the line still being recognised: its text grows with every partial result, and is
      // truncated from the start so the newest words stay on screen
      line.className = 'subtitle-line partial in-progress' + (this.partial.provisional ? ' provisional' : '');
      line.textContent = this._fit(this.partial.text, this.partial.lang);
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
   * A draft translation line: the role's colour, marked as a draft in progress, not editable.
   */
  _createDraftLine(translation, type, cueId, langCode, dimmed) {
    const line = document.createElement('div');
    line.className = `subtitle-line ${type} draft in-progress` + (dimmed ? ' provisional' : '');
    line.textContent = this._fit(this._translationText(translation), langCode);
    line.dataset.cueId = cueId;
    line.dataset.type = type;
    line.dataset.lang = langCode;
    line.dataset.state = 'draft';
    return line;
  }

  /**
   * Create a subtitle line element.
   * @param {string} text - The text to display
   * @param {string} type - 'primary', 'secondary', or 'original'
   * @param {string} cueId - The cue ID
   * @param {string} langCode - Optional language code for styling
   */
  _createSubtitleLine(text, type, cueId, langCode = null, revised = false, fitLang = langCode) {
    const line = document.createElement('div');
    line.className = `subtitle-line ${type}` + (revised ? ' revised' : '');
    line.textContent = this._fit(text, fitLang);
    line.dataset.cueId = cueId;
    line.dataset.type = type;
    if (langCode) {
      line.dataset.lang = langCode;
    }

    // Handle edit mode
    if (this.editMode && this.editCueId === cueId && type !== 'original') {
      line.classList.add('editing');
      line.textContent = text;   // the whole translation is edited, not the rows that fit
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
