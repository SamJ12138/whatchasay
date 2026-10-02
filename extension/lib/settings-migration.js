/**
 * settings-migration.js: settings changes that need more than a new default (D4).
 *
 * Cloud API keys no longer live in the extension: they belong to the backend's
 * config (backend/.env, SUBTITLE_CLOUD__* / SUBTITLE_REFINER__*), are never sent
 * over ws or HTTP, and are never in a URL. stripCloudKeys removes any keys an
 * older version stored and leaves a notice for the options page saying how many
 * were removed and where to put them.
 *
 * Pure: loaded by the service worker, the options page and node tests.
 */
(function (root) {
  const KEY_FIELDS = ['gladia_api_key', 'elevenlabs_api_key', 'google_api_key', 'azure_translator_key',
    'groq_api_key', 'gemini_api_key'];

  const WHERE = 'Cloud API keys now live in the backend, not the extension: put them in backend/.env ' +
    '(for example SUBTITLE_CLOUD__ENABLED=true and SUBTITLE_CLOUD__GOOGLE_API_KEY=...) and restart the backend.';

  /** {settings, removed}: settings without cloudKeys; removed = number of non-empty keys dropped. */
  function stripCloudKeys(settings) {
    if (!settings || !('cloudKeys' in settings)) return { settings, removed: 0 };
    const keys = settings.cloudKeys || {};
    const removed = KEY_FIELDS.filter((k) => typeof keys[k] === 'string' && keys[k].trim()).length;
    const out = { ...settings };
    delete out.cloudKeys;
    if (removed) out.keysRemovedNotice = { count: removed, at: new Date().toISOString() };
    return { settings: out, removed };
  }

  /** Schema 5 (the compact overlay, docs/overlay/README.md): the source line is off by
   *  default. Applied once, to settings written by an older schema; the user's later choice
   *  is kept. Returns the settings unchanged when nothing applies. */
  function applyOverlayDefaults(settings, fromVersion) {
    if (!settings || !(fromVersion < 5)) return settings;
    return { ...settings, showOriginal: false };
  }

  const api = { stripCloudKeys, applyOverlayDefaults, KEY_FIELDS, WHERE };
  root.STSettingsMigration = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : self);
