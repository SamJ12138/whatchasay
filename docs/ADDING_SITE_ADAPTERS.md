# Adding Custom Site Adapters

This guide explains how to add support for websites with non-standard subtitle rendering.

## How Subtitle Detection Works

The extension uses a two-tier detection system:

1. **HTML5 TextTrack API** (Primary)
   - Monitors `<video>` elements for TextTrack cue changes
   - Works with standard WebVTT/TTML subtitles
   - Most reliable method

2. **DOM MutationObserver** (Fallback)
   - Watches for text changes in subtitle container elements
   - Required for sites that render subtitles as DOM elements
   - Needs CSS selectors specific to each site

## When You Need a Custom Adapter

You need a custom adapter when:
- Subtitles don't use standard `<track>` elements
- Subtitles are rendered as overlaid `<div>` or `<span>` elements
- The site uses a custom video player (not HTML5 native)
- TextTrack events don't fire properly

## Creating a Site Adapter

### Step 1: Identify Subtitle Elements

1. Open the video page with subtitles enabled
2. Open DevTools (F12) → Elements tab
3. Look for elements that contain subtitle text
4. Note the CSS selector path

Common patterns:
```
Netflix:      .player-timedtext-text-container
Prime Video:  .atvwebplayersdk-captions-text
Disney+:      .shaka-text-container
Crunchyroll:  .vjs-text-track-display
```

### Step 2: Create Adapter File

Create a new file in `extension/adapters/` named after the site:

```javascript
// extension/adapters/netflix.js

/**
 * Netflix Subtitle Adapter
 * Handles Netflix's custom subtitle rendering
 */

const NetflixAdapter = {
  // Unique identifier
  name: 'netflix',
  
  // URL patterns this adapter handles
  patterns: [
    /^https?:\/\/(www\.)?netflix\.com/
  ],
  
  // CSS selector for subtitle container
  subtitleSelector: '.player-timedtext-text-container',
  
  // CSS selector for individual subtitle lines (optional)
  lineSelector: 'span',
  
  // Video element selector (if non-standard)
  videoSelector: 'video',
  
  // Priority (higher = checked first)
  priority: 100,
  
  /**
   * Check if this adapter should handle current page
   * @returns {boolean}
   */
  matches() {
    return this.patterns.some(p => p.test(window.location.href));
  },
  
  /**
   * Initialize adapter
   * @param {SubtitleDetector} detector - Main detector instance
   */
  init(detector) {
    console.log('[NetflixAdapter] Initializing...');
    this.detector = detector;
    this.setupObserver();
  },
  
  /**
   * Set up DOM observer for subtitle changes
   */
  setupObserver() {
    // Wait for subtitle container to appear
    const checkContainer = setInterval(() => {
      const container = document.querySelector(this.subtitleSelector);
      if (container) {
        clearInterval(checkContainer);
        this.observeSubtitles(container);
      }
    }, 500);
    
    // Stop checking after 30 seconds
    setTimeout(() => clearInterval(checkContainer), 30000);
  },
  
  /**
   * Observe subtitle element for changes
   * @param {Element} container - Subtitle container element
   */
  observeSubtitles(container) {
    const observer = new MutationObserver((mutations) => {
      this.handleSubtitleChange(container);
    });
    
    observer.observe(container, {
      childList: true,
      subtree: true,
      characterData: true
    });
    
    console.log('[NetflixAdapter] Observer attached');
  },
  
  /**
   * Handle subtitle content change
   * @param {Element} container - Subtitle container
   */
  handleSubtitleChange(container) {
    const text = this.extractText(container);
    
    if (text && text.trim()) {
      // Get video element for timing
      const video = document.querySelector(this.videoSelector);
      const currentTime = video ? video.currentTime : 0;
      
      // Create cue object
      const cue = {
        id: `netflix-${Date.now()}`,
        startTime: currentTime,
        endTime: currentTime + 5, // Estimate
        text: text.trim()
      };
      
      // Send to detector
      this.detector.handleCue(cue);
    }
  },
  
  /**
   * Extract text from subtitle container
   * @param {Element} container - Subtitle container
   * @returns {string} Extracted text
   */
  extractText(container) {
    if (this.lineSelector) {
      const lines = container.querySelectorAll(this.lineSelector);
      return Array.from(lines)
        .map(el => el.textContent)
        .join('\n');
    }
    return container.textContent;
  },
  
  /**
   * Clean up adapter
   */
  destroy() {
    // Clean up observers, etc.
  }
};

// Export for use in content script
window.SubtitleAdapters = window.SubtitleAdapters || [];
window.SubtitleAdapters.push(NetflixAdapter);
```

### Step 3: Register the Adapter

Add the adapter to `manifest.json`:

```json
{
  "content_scripts": [
    {
      "matches": ["*://*.netflix.com/*"],
      "js": ["adapters/netflix.js", "content-scripts/main.js"],
      "run_at": "document_idle"
    }
  ]
}
```

Or dynamically load in `subtitle-detector.js`:

```javascript
// In SubtitleDetector class
loadAdapters() {
  // Check for registered adapters
  if (window.SubtitleAdapters) {
    for (const adapter of window.SubtitleAdapters) {
      if (adapter.matches()) {
        console.log(`[SubtitleDetector] Using adapter: ${adapter.name}`);
        adapter.init(this);
        this.activeAdapter = adapter;
        return true;
      }
    }
  }
  return false;
}
```

## Adapter API Reference

### Required Properties

| Property | Type | Description |
|----------|------|-------------|
| `name` | string | Unique identifier |
| `patterns` | RegExp[] | URL patterns to match |
| `subtitleSelector` | string | CSS selector for subtitle container |

### Optional Properties

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `lineSelector` | string | null | CSS selector for subtitle lines |
| `videoSelector` | string | 'video' | CSS selector for video element |
| `priority` | number | 0 | Adapter priority (higher = first) |

### Required Methods

| Method | Parameters | Returns | Description |
|--------|------------|---------|-------------|
| `matches()` | - | boolean | Check if adapter should handle page |
| `init(detector)` | SubtitleDetector | void | Initialize adapter |

### Optional Methods

| Method | Parameters | Returns | Description |
|--------|------------|---------|-------------|
| `extractText(container)` | Element | string | Custom text extraction |
| `destroy()` | - | void | Cleanup on unload |

## Testing Your Adapter

1. Load the extension with your adapter
2. Open the target site with a video
3. Enable subtitles
4. Open DevTools Console
5. Look for adapter initialization logs:
   ```
   [YourAdapter] Initializing...
   [YourAdapter] Observer attached
   ```
6. Check that subtitle cues are being detected

## Debugging Tips

### Log all subtitle changes
```javascript
handleSubtitleChange(container) {
  const text = this.extractText(container);
  console.log('[Adapter] Subtitle:', text);
  // ... rest of handler
}
```

### Check if container exists
```javascript
const container = document.querySelector(this.subtitleSelector);
console.log('[Adapter] Container found:', !!container);
```

### Monitor mutations
```javascript
observeSubtitles(container) {
  const observer = new MutationObserver((mutations) => {
    console.log('[Adapter] Mutations:', mutations.length);
    mutations.forEach(m => console.log(m.type, m.target));
    this.handleSubtitleChange(container);
  });
  // ...
}
```

## Common Issues

### Subtitles not detected
- Selector may be wrong - verify in DevTools
- Container may load dynamically - add retry logic
- Site may use shadow DOM - need special handling

### Duplicate subtitles
- Add debouncing to `handleSubtitleChange`
- Track last subtitle text and skip if identical

### Timing issues
- Video element may not be accessible
- Use estimated timing based on subtitle duration

## Example Adapters

### Prime Video
```javascript
const PrimeVideoAdapter = {
  name: 'primevideo',
  patterns: [/amazon\.(com|co\.\w+)\/.*\/video/],
  subtitleSelector: '.atvwebplayersdk-captions-text',
  lineSelector: 'span.atvwebplayersdk-captions-text',
  // ...
};
```

### Disney+
```javascript
const DisneyPlusAdapter = {
  name: 'disneyplus',
  patterns: [/disneyplus\.com/],
  subtitleSelector: '[data-testid="player-captions-container"]',
  // ...
};
```

### Crunchyroll
```javascript
const CrunchyrollAdapter = {
  name: 'crunchyroll',
  patterns: [/crunchyroll\.com\/watch/],
  subtitleSelector: '.vjs-text-track-display',
  // ...
};
```

## Contributing Adapters

If you create an adapter for a popular site, please contribute it!

1. Test thoroughly on the target site
2. Add comments explaining site-specific quirks
3. Include the site's subtitle format documentation (if available)
4. Submit a pull request

Note: Some streaming services actively prevent subtitle extraction. Adapters may break when sites update their players. Please report issues on GitHub.
