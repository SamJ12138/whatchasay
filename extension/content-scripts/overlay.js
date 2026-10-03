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
      rowsPerLine: 2,          // a line may wrap once; beyond that it keeps its newest words
      maxLines: 2,             // (the backend line breaker's setting; the overlay draws rowsPerLine)
      maxCharsLatin: 42,       // characters per row
      maxCharsCJK: 22,         // ...for zh / ja / ko
      minDisplayS: 1.5,        // a final translation stays at least this long...
      readCharsPerS: 15,       // ...or its length / this many characters per second
      minDisplayFloorS: 1.0,   // ...shortened toward this floor when lines queue up behind it (fast speech)
      // Dynamic colors per language
      primaryColor: '#ffffff',
      secondaryColor: '#ffeb3b',
      // Legacy en/zh colors for backwards compatibility
      enColor: '#ffffff',
      zhColor: '#ffeb3b',
      originalColor: '#dddddd',
      borderRadius: '0.35em',
      lineHeight: 1.2,
    };

    // The rolling two-row layout (lib/overlay-rows.js): row 1 the previous line's final
    // translation, row 2 the current line's draft (or its final while row 1 is held).
    this.rows = this._newRows();
    this._now = () => Date.now() / 1000;
    this._rowTimer = null;

    // DOM elements
    this.hostElement = null;
    this.shadowRoot = null;
    this.container = null;
    this.subtitleStack = null;

    // Edit state (Alt+E: the translation of a line, or, with a word selected in the
    // recognised line, a glossary term: termEditor = {heard, cueId, sourceLang, renderingLang, fields})
    this.editMode = false;
    this.editCueId = null;
    this.termEditor = null;
    this.onCorrectionCallback = null;
    this.onGlossaryTermCallback = null;

    // Position tracking (docs/overlay/README.md): the block sits below the video in the
    // page's space, or over its bottom when the video is fullscreen / fills the viewport;
    // above the site's control bar when that is showing; draggable, the offset remembered
    // per site origin and placement mode.
    this.videoElement = null;
    this.resizeObserver = null;
    this.videoObserver = null;
    this.block = null;
    this.extras = null;
    this.placementMode = null;      // 'page' | 'overlay' | 'viewport'
    this.lastPlacement = null;
    this.dragging = false;
    this._drag = null;
    this.dragOffsets = {};          // mode -> {dx, dy} for this origin
    this._origin = (typeof window !== 'undefined' && window.location && window.location.origin) || '';
    this._positionTimer = null;
    this._positionRaf = null;

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
  _newRows() {
    const R = globalThis.STOverlayRows;
    if (R) return R.createRows({ minDisplayS: this.settings.minDisplayS, charsPerS: this.settings.readCharsPerS, floorS: this.settings.minDisplayFloorS });
    // no module (an old injection): the newest line only
    let only = null;
    return { open(c) { only = { cueId: c, kind: 'draft' }; }, final(c) { only = { cueId: c, kind: 'final' }; },
      remove() { only = null; }, clear() { only = null; }, tick() { return false; }, nextWake() { return null; },
      view() { return { row1: only && only.kind === 'final' ? { cueId: only.cueId } : null, row2: only && only.kind === 'draft' ? only : null }; },
      minDisplay() { return 0; } };
  }

  /** Row 1's hold may be over: let the rows move and redraw. */
  _tickRows() {
    if (this.rows.tick(this._now())) this._updateDisplay();
    else this._scheduleRowTick();
  }

  _scheduleRowTick() {
    if (this._rowTimer) { clearTimeout(this._rowTimer); this._rowTimer = null; }
    const wake = this.rows.nextWake();
    if (wake == null) return;
    const ms = Math.max(0, (wake - this._now()) * 1000) + 10;
    this._rowTimer = setTimeout(() => { this._rowTimer = null; this._tickRows(); }, ms);
    if (this._rowTimer && typeof this._rowTimer.unref === 'function') this._rowTimer.unref();
  }

  init() {
    if (this.hostElement) {
      console.log('[Overlay] Already initialized');
      return;
    }

    console.log('[Overlay] Initializing');
    this._createOverlay();
    this._setupResizeObserver();
    this._injectStyles();
    this.subtitleStack.style.height = `${this.reservedHeight()}px`;
    this._loadOffsets();
    this._updatePosition();
  }

  /**
   * Destroy the overlay.
   */
  destroy() {
    if (this.resizeObserver) {
      this.resizeObserver.disconnect();
    }
    if (this.videoObserver) {
      this.videoObserver.disconnect();
    }
    if (this._positionTimer) clearInterval(this._positionTimer);
    if (this._rowTimer) clearTimeout(this._rowTimer);

    if (this.hostElement) {
      this.hostElement.remove();
    }

    this.hostElement = null;
    this.shadowRoot = null;
    this.container = null;
    this.subtitleStack = null;
    this.block = null;
    this.extras = null;
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

    const now = this._now();
    const shown = this._finalText(cueId);
    if (shown != null) this.rows.final(cueId, shown.length, now);   // the length the viewer has to read
    else this.rows.open(cueId, this._hasDraftText(cueId), now);     // the final caption, its translation on its way
    // the cues the rows can still show, plus a few for late revisions
    while (this.currentCues.size > 8) {
      const oldest = this.currentCues.keys().next().value;
      const v = this.rows.view();
      if ((v.row1 && v.row1.cueId === oldest) || (v.row2 && v.row2.cueId === oldest)) break;
      if (this.editMode && this.editCueId === oldest) break;   // never drop the cue being corrected
      this.currentCues.delete(oldest);
      this.drafts.delete(oldest);
    }

    // Update display
    this._updateDisplay();
    return true;
  }

  /** The final translation drawn for a cue (the primary role's, fitted), or null. */
  _finalText(cueId) {
    const cue = this.currentCues.get(cueId);
    if (!cue) return null;
    const primary = this.primaryOnTop ? this.primaryLang : this.secondaryLang;
    const secondary = this.primaryOnTop ? this.secondaryLang : this.primaryLang;
    for (const lang of [primary, secondary]) {
      if (lang && lang !== 'none' && this._usable(cue.translations[lang])) return this._fit(this._translationText(cue.translations[lang]), lang);
    }
    return null;
  }

  _hasDraftText(cueId) {
    const d = this.drafts.get(cueId);
    return !!d && Object.keys(d.translations).some((l) => this._usable(d.translations[l]));
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
    if (this._finalText(cueId) == null) this.rows.open(cueId, this._hasDraftText(cueId), this._now());
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
    if (this._finalText(cueId) == null) this.rows.open(cueId, true, this._now());
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
        if (keep) cue.provisional = false; else { this.currentCues.delete(id); this.rows.remove(id); }
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
    this.rows.remove(cueId);
    if (this.partial && this.partial.cueId === cueId) this.partial = null;
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
    this.rows.clear();
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
    this._updateStyles();   // the reserved rows change
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
    const r = layout.fitLines(t, { maxLines: this.settings.rowsPerLine || 1, cjk,
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
    if ('minDisplayS' in newSettings || 'readCharsPerS' in newSettings || 'minDisplayFloorS' in newSettings) {
      const keep = this.rows.view();
      this.rows = this._newRows();
      if (keep.row1) this.rows.final(keep.row1.cueId, 0, this._now() - 1e6);
      if (keep.row2) { if (keep.row2.kind === 'final') this.rows.final(keep.row2.cueId, 0, this._now() - 1e6); else this.rows.open(keep.row2.cueId, true, this._now()); }
    }
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
    this.termEditor = null;
    this._updateDisplay();
  }

  /**
   * Set callback for corrections.
   */
  onCorrection(callback) {
    this.onCorrectionCallback = callback;
  }

  /** Set the callback for glossary terms taught with Alt+E (lib/glossary.js termFromSelection). */
  onGlossaryTerm(callback) {
    this.onGlossaryTermCallback = callback;
  }

  // ---- the glossary's term editor: Alt+E with a word selected in the recognised line ----

  _isSourceLine(el) {
    for (let n = el; n && n.classList; n = n.parentNode) {
      if (n.classList.contains('partial') || n.classList.contains('original')) return true;
      if (n.classList.contains('subtitle-line')) return false;
    }
    return false;
  }

  /**
   * The text selected in the recognised line (the partial or the original row), with its
   * cue, or null. Chrome keeps a closed shadow root's selection on the root itself.
   */
  selectedSourceText() {
    let sel = null;
    try {
      sel = this.shadowRoot && typeof this.shadowRoot.getSelection === 'function' ? this.shadowRoot.getSelection()
        : (typeof document !== 'undefined' && document.getSelection ? document.getSelection() : null);
    } catch (_) { sel = null; }
    if (!sel || sel.isCollapsed) return null;
    const text = String(sel.toString() || '').trim();
    if (!text) return null;
    let node = sel.anchorNode;
    while (node && !(node.classList && node.classList.contains('subtitle-line'))) node = node.parentNode;
    if (!node || !this._isSourceLine(node)) return null;
    return { text, cueId: node.dataset ? node.dataset.cueId : null };
  }

  /**
   * Open the term editor for the selection: "heard as <selection>: should be [ ], in <lang> [ ]".
   * Returns false when nothing is selected in the recognised line (Alt+E then edits the
   * translation as before).
   */
  enableTermEdit() {
    const sel = this.selectedSourceText();
    if (!sel || !sel.text) return false;
    const cue = sel.cueId ? this.currentCues.get(sel.cueId) : null;
    const sourceLang = (cue && cue.sourceLang) || (this.partial && this.partial.lang) || this.liveLang || null;
    const primary = this.primaryOnTop ? this.primaryLang : this.secondaryLang;
    const secondary = this.primaryOnTop ? this.secondaryLang : this.primaryLang;
    const renderingLang = [primary, secondary].find((l) => l && l !== 'none' && l !== sourceLang) || null;
    this.editMode = true;
    this.editCueId = null;
    this.termEditor = { heard: sel.text, cueId: sel.cueId || null, sourceLang, renderingLang, fields: {} };
    this._updateDisplay();
    return true;
  }

  _createTermEditor() {
    const ed = this.termEditor;
    const line = document.createElement('div');
    line.className = 'subtitle-line term-edit editing';
    line.dataset.state = 'term-edit';
    const label = (text) => {
      const s = document.createElement('span');
      s.className = 'term-label';
      s.textContent = text;
      return s;
    };
    const field = (name, value) => {
      const f = document.createElement('span');
      f.className = 'term-field';
      f.textContent = value;
      f.contentEditable = 'true';
      f.spellcheck = false;
      f.dataset.field = name;
      f.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          if (typeof e.preventDefault === 'function') e.preventDefault();
          this._saveTermEdit();
        } else if (e.key === 'Escape') {
          this.disableEditMode();
        }
      });
      // typed text, not the page's player shortcuts (observations E18)
      for (const type of ['keydown', 'keypress', 'keyup']) f.addEventListener(type, (e) => e.stopPropagation());
      ed.fields[name] = f;
      return f;
    };
    line.appendChild(label(`heard “${ed.heard}”, should be`));
    line.appendChild(field('canonical', ed.heard));
    if (ed.renderingLang) {
      line.appendChild(label(`shown in ${this._languageName(ed.renderingLang)} as`));
      line.appendChild(field('rendering', ''));
    }
    line.appendChild(label('Enter saves, Esc cancels'));
    for (const type of ['keydown', 'keypress', 'keyup']) line.addEventListener(type, (e) => e.stopPropagation());
    this._editingLine = line;
    setTimeout(() => { try { ed.fields.canonical.focus(); } catch (_) {} }, 0);
    return line;
  }

  _languageName(code) {
    const names = { en: 'English', zh: 'Chinese', bn: 'Bengali', vi: 'Vietnamese', ja: 'Japanese', ko: 'Korean', es: 'Spanish',
      fr: 'French', de: 'German', ru: 'Russian', pt: 'Portuguese', it: 'Italian' };
    return names[code] || code;
  }

  _saveTermEdit() {
    const ed = this.termEditor;
    if (!ed) return;
    const G = globalThis.STGlossary;
    const term = G ? G.termFromSelection({
      selected: ed.heard, sourceLang: ed.sourceLang,
      canonical: ed.fields.canonical ? ed.fields.canonical.textContent : '',
      renderingLang: ed.renderingLang,
      rendering: ed.fields.rendering ? ed.fields.rendering.textContent : '',
    }) : null;
    this.disableEditMode();
    if (term && this.onGlossaryTermCallback) this.onGlossaryTermCallback(term);
  }

  /**
   * Set the video element for positioning.
   */
  setVideoElement(video) {
    if (video === this.videoElement) return;
    this.videoElement = video;
    if (this.videoObserver) {
      this.videoObserver.disconnect();
      this.videoObserver = null;
    }
    if (video && typeof ResizeObserver !== 'undefined') {
      try {
        this.videoObserver = new ResizeObserver(() => { this._updateStyles(); this._updatePosition(); });
        this.videoObserver.observe(video);
      } catch (_) { this.videoObserver = null; }
    }
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

    // Create container: the extras (notices, the language label) and the subtitle block
    this.container = document.createElement('div');
    this.container.className = 'overlay-container';

    this.extras = document.createElement('div');
    this.extras.className = 'overlay-extras';

    // the draggable block that holds the caption lines
    this.block = document.createElement('div');
    this.block.className = 'subtitle-block';

    this.subtitleStack = document.createElement('div');
    this.subtitleStack.className = 'subtitle-stack';

    this.block.appendChild(this.subtitleStack);
    this.container.appendChild(this.extras);
    this.container.appendChild(this.block);
    this.shadowRoot.appendChild(this.container);
    this._setupDrag();

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
    const grid = this._gridRows();
    return `
      .overlay-container {
        position: fixed;
        bottom: 15%;
        left: 10%;
        width: 80%;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 4px;
        pointer-events: none;
      }

      /* below the video: the extras go under the block; over it: above the block */
      .overlay-container.mode-page {
        flex-direction: column-reverse;
      }

      /* the rows pack against the video: at the top of the reserved box below it, at the bottom over it */
      .mode-page .subtitle-stack {
        align-content: start;
      }

      .overlay-extras {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 2px;
        max-width: 100%;
      }

      .overlay-extras:empty {
        display: none;
      }

      /* the block the viewer can drag */
      .subtitle-block {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: flex-end;
        width: 100%;
        box-sizing: border-box;
        pointer-events: auto;
        cursor: grab;
        touch-action: none;
        user-select: none;
      }

      .subtitle-block:has(.subtitle-stack:empty) {
        pointer-events: none;
      }

      /* the rolling rows: a grid (row 1, row 2, per role; the source row) in a box of fixed height,
         rowsPerLine visual rows per line, so the block never changes height while text streams; the
         tracks are sized by their text and packed against the video, so one-row lines sit next to
         each other and a line that wraps grows into the reserved space */
      .subtitle-stack {
        width: 100%;
        height: ${this.reservedHeight()}px;
        display: grid;
        grid-template-rows: ${grid.template};
        row-gap: ${grid.gapPx}px;
        align-content: end;
        align-items: end;
        justify-items: center;
        font-size: ${text.fontPx}px;
      }

${grid.rules}

      /* a rounded box behind the text only, not a bar across the picture */
      .subtitle-line {
        display: inline-block;
        width: fit-content;
        max-width: 100%;
        box-sizing: border-box;
        font-family: ${this.settings.fontFamily};
        font-size: ${text.fontPx}px;
        line-height: ${this.settings.lineHeight};
        padding: 0.1em 0.5em;
        background: ${bg};
        border-radius: ${this.settings.borderRadius};
        text-align: center;
        white-space: pre-wrap;
        overflow-wrap: break-word;
        text-shadow: 0 0 2px rgba(0,0,0,0.9);
        pointer-events: auto;
      }

      /* the recognised line can be selected with the mouse (Alt+E then teaches the glossary) */
      .subtitle-line.partial, .subtitle-line.original {
        user-select: text;
        cursor: text;
      }

      /* the term editor: "heard as X" and two fields the user types into */
      .subtitle-line.term-edit {
        color: #ffffff;
        font-size: 0.85em;
        white-space: nowrap;
        user-select: text;
      }
      .subtitle-line.term-edit .term-field {
        display: inline-block;
        min-width: 3em;
        margin: 0 0.3em;
        padding: 0 0.3em;
        border-bottom: 1px solid rgba(255,255,255,0.7);
        outline: none;
        color: #ffeb3b;
      }
      .subtitle-line.term-edit .term-label {
        opacity: 0.8;
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
      .subtitle-line.original,
      .subtitle-line.row-source {
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
    if (this.subtitleStack) this.subtitleStack.style.height = `${this.reservedHeight()}px`;
  }

  /**
   * Update the display from the rows (lib/overlay-rows.js): row 1 the previous line's
   * final translation, row 2 the current line's draft (or its final while row 1 is held),
   * and, when the source line is on, a third row with the current line's words.
   */
  _updateDisplay() {
    if (!this.subtitleStack) return;
    // While the user is typing a correction, new captions must not rebuild (and so
    // discard) the line being edited; the display catches up when the edit ends.
    if (this.editMode && this._editingLine && this._editingLine.isConnected) return;

    this.rows.tick(this._now());
    const v = this.rows.view();
    const fresh = [];   // the lines wanted, top to bottom; reconciled with what is drawn below
    const primary = this.primaryOnTop ? this.primaryLang : this.secondaryLang;
    const secondary = this.primaryOnTop ? this.secondaryLang : this.primaryLang;
    const roles = [['primary', primary], ['secondary', secondary]].filter(([, lang]) => lang && lang !== 'none');

    const place = (line, row, role) => { line.classList.add(`row-${row}-${role}`); line.dataset.row = row; return line; };
    let drewRow2 = false;
    for (const [row, entry] of [['1', v.row1 ? Object.assign({ kind: 'final' }, v.row1) : null], ['2', v.row2]]) {
      if (!entry) continue;
      const cueId = entry.cueId;
      const cue = this.currentCues.get(cueId);
      const draft = this.drafts.get(cueId);
      for (const [role, lang] of roles) {
        if (entry.kind === 'final' && cue && this._usable(cue.translations[lang])) {
          const line = this._createSubtitleLine(this._translationText(cue.translations[lang]), role, cueId, lang, !!cue.revised);
          if (cue.provisional) line.classList.add('provisional');
          fresh.push(place(line, row, role));
        } else if (draft && this._usable(draft.translations[lang])) {
          const dimmed = draft.provisional || !!(cue && cue.provisional) || !!(this.partial && this.partial.cueId === cueId && this.partial.provisional);
          fresh.push(place(this._createDraftLine(draft.translations[lang], role, cueId, lang, dimmed), row, role));
        } else continue;
        if (row === '2') drewRow2 = true;
      }
      // a current line with nothing to translate (its final caption, no translation, no
      // draft) shows its words in row 2, so the viewer is never left with nothing
      if (row === '2' && !drewRow2 && cue && cue.original && !this.showOriginal) {
        const line = this._createSubtitleLine(cue.original, 'original', cueId, null, !!cue.revised, cue.sourceLang);
        if (cue.provisional) line.classList.add('provisional');
        fresh.push(place(line, row, 'primary'));
        drewRow2 = true;
      }
    }

    // the source row: the line being recognised, else the words of the current line
    if (this.showOriginal) {
      const current = v.row2 ? v.row2.cueId : (v.row1 ? v.row1.cueId : null);
      if (this.partial && this.partial.text) {
        const line = document.createElement('div');
        line.className = 'subtitle-line partial in-progress row-source' + (this.partial.provisional ? ' provisional' : '');
        line.textContent = this._fit(this.partial.text, this.partial.lang);
        line.dataset.cueId = this.partial.cueId;
        line.dataset.state = 'in-progress';
        line.dataset.row = 'source';
        fresh.push(line);
      } else if (current && this.currentCues.get(current) && this.currentCues.get(current).original) {
        const cue = this.currentCues.get(current);
        const line = this._createSubtitleLine(cue.original, 'original', current, null, !!cue.revised, cue.sourceLang);
        line.classList.add('row-source');
        line.dataset.row = 'source';
        if (cue.provisional) line.classList.add('provisional');
        fresh.push(line);
      }
    }

    // the glossary's term editor, under the rows, while it is open
    if (this.editMode && this.termEditor) fresh.push(this._createTermEditor());

    this._reconcile(fresh);
    this._renderExtras();
    this._scheduleRowTick();
  }

  /**
   * Put the wanted lines on screen with the least change: a drawn line of the same kind
   * (classes, cue, role, language) keeps its element and only its text is updated, so a
   * growing partial or a newer draft changes in place; anything else is replaced. The
   * line being edited is never reused (its listeners and contenteditable are its own).
   */
  _reconcile(fresh) {
    const stack = this.subtitleStack;
    const same = (a, b) => a.className === b.className && a.dataset.cueId === b.dataset.cueId
      && a.dataset.type === b.dataset.type && a.dataset.lang === b.dataset.lang && a.dataset.state === b.dataset.state
      && !a.className.split(/\s+/).includes('editing');
    let i = 0;
    for (; i < fresh.length; i++) {
      const have = stack.children[i];
      if (have && same(have, fresh[i])) {
        if (have.textContent !== fresh[i].textContent) have.textContent = fresh[i].textContent;
        continue;
      }
      break;
    }
    while (stack.children.length > i) stack.removeChild(stack.children[stack.children.length - 1]);
    for (; i < fresh.length; i++) stack.appendChild(fresh[i]);
  }

  /**
   * The language label and any notice, in the extras next to the block.
   */
  _renderExtras() {
    const extras = this.extras || this.subtitleStack;
    if (this.extras) this.extras.innerHTML = '';
    const label = this._languageLabel();
    if (label) {
      const line = document.createElement('div');
      line.className = 'subtitle-line notice lang-pending' + (label.kind === 'warn' ? ' warn' : '');
      line.textContent = label.text;
      extras.appendChild(line);
    }
    if (this.notice && this.notice.text) {
      const line = document.createElement('div');
      line.className = 'subtitle-line notice' + (this.notice.kind === 'warn' ? ' warn' : '');
      line.textContent = this.notice.text;
      extras.appendChild(line);
    }
    this._schedulePosition();
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
    // the video's place on screen changes with scrolling, resizing and fullscreen; the
    // site's control bar comes and goes with the mouse
    try {
      const reposition = () => this._schedulePosition();
      window.addEventListener('scroll', reposition, { passive: true, capture: true });
      window.addEventListener('resize', reposition, { passive: true });
      document.addEventListener('fullscreenchange', reposition);
      document.addEventListener('mousemove', reposition, { passive: true });
    } catch (_) {}
    this._positionTimer = setInterval(() => {
      if (this.currentCues.size || this.partial || this.drafts.size) this._updatePosition();
    }, 250);
    // under node (tests) a live interval would keep the process alive
    if (this._positionTimer && typeof this._positionTimer.unref === 'function') this._positionTimer.unref();
  }

  _schedulePosition() {
    if (this._positionRaf) return;
    const raf = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : (f) => setTimeout(f, 16);
    this._positionRaf = raf(() => { this._positionRaf = null; this._updatePosition(); });
  }

  _videoRect() {
    if (!this.videoElement || typeof this.videoElement.getBoundingClientRect !== 'function') return null;
    try {
      const r = this.videoElement.getBoundingClientRect();
      return { x: r.left, y: r.top, w: r.width, h: r.height };
    } catch (_) { return null; }
  }

  _isFullscreen() {
    const fs = typeof document !== 'undefined' ? document.fullscreenElement : null;
    if (!fs) return false;
    if (!this.videoElement) return true;
    try { return fs === this.videoElement || (typeof fs.contains === 'function' && fs.contains(this.videoElement)); } catch (_) { return true; }
  }

  /**
   * The height the stack reserves, in px: rowsPerLine visual rows for row 1 and row 2 of
   * each translation role that is configured, and rowsPerLine rows of the source font when
   * the source line is on, each with its line box's padding, plus the gaps between them.
   * The stack is given this height, so the block never changes height while text streams
   * (batch 3); the lines pack against the video inside it (grid tracks sized by their text).
   */
  reservedHeight() {
    return this._gridRows().heightPx;
  }

  _roles() {
    const roles = ['primary'];
    if (this.secondaryLang && this.secondaryLang !== 'none' && this.secondaryLang !== this.primaryLang) roles.push('secondary');
    return roles;
  }

  /**
   * The grid's rows: for row 1 and row 2, one track per translation role, then the source
   * row when it is on; each track sized by its text (`auto`), and rowsPerLine rows of text
   * with the box's padding reserved for it. {template, rules (grid-row per row class),
   * gapPx, heightPx (the reservation)}.
   */
  _gridRows() {
    const rows = Math.max(1, this.settings.rowsPerLine || 1);
    const text = this.roleStyle('primary').fontPx;
    const source = this.roleStyle('original').fontPx;
    const box = (px) => rows * px * this.settings.lineHeight + px * 0.2;   // padding 0.1em top and bottom
    const gapPx = Math.round(text * 0.1 * 10) / 10;
    const heights = [];
    const rules = [];
    for (const r of ['1', '2']) {
      for (const role of this._roles()) {
        heights.push(box(text));
        rules.push(`      .subtitle-line.row-${r}-${role} { grid-row: ${heights.length}; }`);
      }
    }
    if (this.showOriginal) {
      heights.push(box(source));
      rules.push(`      .subtitle-line.row-source { grid-row: ${heights.length}; }`);
    }
    const total = heights.reduce((a, b) => a + b, 0) + (heights.length - 1) * gapPx;
    return { template: heights.map(() => 'auto').join(' '), rules: rules.join('\n'), gapPx,
             heightPx: Math.ceil(total) };
  }

  /**
   * The block's height for placement: the reserved height (the rendered one is the same
   * once the style sheet is applied).
   */
  blockHeight() {
    return this.reservedHeight();
  }

  /**
   * The site's control bar when it is showing over the video, as a rect, else null.
   * Per-site override first (lib/overlay-layout.js CONTROLS_OVERRIDES), else the generic
   * rule: a bottom-anchored, wide, visible element over the video's bottom edge.
   */
  _controlsRect(video) {
    const layout = globalThis.STOverlayLayout;
    if (!layout || !video || typeof document === 'undefined') return null;
    const rectOf = (el) => { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; };
    const visible = (el) => {
      try {
        const cs = getComputedStyle(el);
        return !(cs.opacity === '0' || cs.visibility === 'hidden' || cs.display === 'none');
      } catch (_) { return true; }
    };
    const override = layout.controlsOverride(this._origin);
    if (override) {
      try {
        const el = document.querySelector(override.selector);
        if (!el) return null;
        const player = override.player ? el.closest(override.player) : null;
        if (override.hiddenClass && player && player.classList.contains(override.hiddenClass)) return null;
        if (!visible(el)) return null;
        const r = rectOf(el);
        return r.h > 0 ? r : null;
      } catch (_) { return null; }
    }
    if (typeof document.elementsFromPoint !== 'function') return null;
    const candidates = [];
    const seen = new Set();
    for (const fx of [0.5, 0.25, 0.75]) {
      let els = [];
      try { els = document.elementsFromPoint(video.x + video.w * fx, video.y + video.h - 10); } catch (_) {}
      for (const el of els) {
        if (el === this.videoElement) break;   // only what is drawn over the video
        if (seen.has(el) || el === this.hostElement) continue;
        seen.add(el);
        let opacity = 1;
        try { opacity = parseFloat(getComputedStyle(el).opacity); } catch (_) {}
        candidates.push({ rect: rectOf(el), opacity: Number.isNaN(opacity) ? 1 : opacity, hidden: !visible(el) });
      }
    }
    return layout.pickControls(candidates, video);
  }

  /**
   * Put the container where the video is not: below a page video, over the bottom of a
   * fullscreen one (lib/overlay-layout.js placement), the dragged offset added.
   */
  _updatePosition() {
    if (!this.container) return;
    const layout = globalThis.STOverlayLayout;
    if (!layout) return;
    const video = this._videoRect();
    const viewport = { w: window.innerWidth || 0, h: window.innerHeight || 0 };
    const fullscreen = this._isFullscreen();
    const fills = !!video && (fullscreen || (video.w >= viewport.w * 0.95 && video.h >= viewport.h * 0.9));
    const controls = video && fills ? this._controlsRect(video) : null;
    const blockHeight = this.blockHeight();
    // the mode first (without the offset), so the offset of that mode applies
    const probe = layout.placement({ video, viewport, fullscreen, blockHeight, controls });
    const offset = this.dragOffsets[probe.mode] || null;
    const p = offset ? layout.placement({ video, viewport, fullscreen, blockHeight, controls, offset }) : probe;
    this.placementMode = p.mode;
    this.lastPlacement = p;
    const st = this.container.style;
    st.left = `${Math.round(p.left)}px`;
    st.width = `${Math.round(p.width)}px`;
    st.maxWidth = 'none';
    st.transform = 'none';
    if (p.top != null) {
      st.top = `${Math.round(p.top)}px`;
      st.bottom = 'auto';
    } else {
      st.top = 'auto';
      st.bottom = `${Math.round(viewport.h - p.bottom)}px`;
    }
    const cls = 'overlay-container mode-' + p.mode;
    if (this.container.className !== cls) this.container.className = cls;
  }

  // ---- drag: the block follows the pointer; the offset is kept per origin and mode ----

  _setupDrag() {
    if (!this.block) return;
    const THRESHOLD = 4;
    this.block.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || this.editMode) return;
      // the recognised line (partial / original) is selectable text for Alt+E's term
      // editor: a press on it starts a selection, not a drag of the block
      if (this._isSourceLine(e.target)) return;
      this._drag = { x: e.clientX, y: e.clientY, moved: false, pointerId: e.pointerId };
    });
    this.block.addEventListener('pointermove', (e) => {
      if (!this._drag) return;
      const dx = e.clientX - this._drag.x, dy = e.clientY - this._drag.y;
      if (!this._drag.moved) {
        if (Math.abs(dx) < THRESHOLD && Math.abs(dy) < THRESHOLD) return;
        this._drag.moved = true;
        this.dragging = true;
        const mode = this.placementMode || 'viewport';
        this._drag.mode = mode;
        this._drag.base = Object.assign({ dx: 0, dy: 0 }, this.dragOffsets[mode] || {});
        try { if (typeof this.block.setPointerCapture === 'function') this.block.setPointerCapture(e.pointerId); } catch (_) {}
      }
      if (typeof e.preventDefault === 'function') e.preventDefault();
      this.dragOffsets[this._drag.mode] = { dx: this._drag.base.dx + dx, dy: this._drag.base.dy + dy };
      this._updatePosition();
    });
    const end = () => {
      if (!this._drag) return;
      const moved = this._drag.moved;
      const mode = this._drag.mode;
      this._drag = null;
      this.dragging = false;
      if (moved) {
        this._saveOffsets();
        console.log('[Overlay] Dragged:', mode, this.dragOffsets[mode]);
      }
    };
    this.block.addEventListener('pointerup', end);
    this.block.addEventListener('pointercancel', end);
  }

  _storage() {
    try { return (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) || null; } catch (_) { return null; }
  }

  _loadOffsets() {
    const storage = this._storage();
    if (!storage || !this._origin) return;
    try {
      storage.get('overlayOffsets', (r) => {
        try { void (chrome.runtime && chrome.runtime.lastError); } catch (_) {}
        const all = (r && r.overlayOffsets) || {};
        if (all[this._origin]) {
          this.dragOffsets = Object.assign({}, all[this._origin]);
          this._updatePosition();
        }
      });
    } catch (_) {}
  }

  _saveOffsets() {
    const storage = this._storage();
    if (!storage || !this._origin) return;
    try {
      storage.get('overlayOffsets', (r) => {
        try { void (chrome.runtime && chrome.runtime.lastError); } catch (_) {}
        const all = Object.assign({}, (r && r.overlayOffsets) || {});
        all[this._origin] = Object.assign({}, this.dragOffsets);
        storage.set({ overlayOffsets: all }, () => { try { void (chrome.runtime && chrome.runtime.lastError); } catch (_) {} });
      });
    } catch (_) {}
  }

}

// Export singleton instance
window.subtitleOverlay = new SubtitleOverlay();
