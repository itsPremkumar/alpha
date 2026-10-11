---
sidebar_position: 3
title: E2B Sandbox
description: Cloud VM sandbox with browser automation
slug: /guides/sandbox/e2b
---

# E2B Sandbox

Cloud VM sandbox with browser automation capabilities.

## Quick Start

```yaml
sandbox:
  provider: "e2b"
  e2b:
    api_key: "${E2B_API_KEY}"
```

## Features

- **Cloud VMs**: Full VM isolation
- **Browser automation**: Playwright/Puppeteer support
- **Persistent sessions**: Session persistence across runs
- **Internet access**: Full internet access
- **GPU support**: Optional GPU acceleration

## Configuration

```yaml
e2b:
  api_key: "${E2B_API_KEY}"
  template: "base"
  timeout: 300
  metadata:
    project: "my-project"
```

## Browser Automation

```python
page = await sandbox.browser.new_page()
await page.goto("https://example.com")
await page.screenshot(path="/workspace/screenshot.png")
content = await page.content()
```