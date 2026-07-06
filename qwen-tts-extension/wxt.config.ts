import { defineConfig } from 'wxt';

const qwenBackendHosts = Array.from({ length: 20 }, (_, index) => {
  const port = 8001 + index;
  return `http://127.0.0.1:${port}/*`;
});

// See https://wxt.dev/api/config.html
export default defineConfig({
  manifest: {
    name: "Qwen TTS Reader",
    permissions: ["contextMenus", "storage", "activeTab", "scripting", "clipboardRead"],
    host_permissions: ["<all_urls>", ...qwenBackendHosts],
    commands: {
      "qwen-tts-read": {
        "suggested_key": {
          "default": "Alt+S",
          "mac": "MacCtrl+S"
        },
        "description": "Read selected text with Qwen TTS"
      }
    }
  },
});
