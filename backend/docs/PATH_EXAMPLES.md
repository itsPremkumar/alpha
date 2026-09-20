# File Path Usage Examples

## Three Path Types

The Alpha file upload system returns three distinct path representations, each tailored to specific operational contexts:

### 1. Actual Filesystem Path (`path`)

```
.agent-workspace/threads/{thread_id}/user-data/uploads/document.pdf
```

**Usage:**
- The actual physical location of the file on the host filesystem.
- Relative to the `backend/` directory.
- Used for direct server filesystem operations, backups, and backend diagnostics.

**Example:**
```python
# Direct access in Python backend code
from pathlib import Path
file_path = Path("backend/.agent-workspace/threads/abc123/user-data/uploads/document.pdf")
content = file_path.read_bytes()
```

### 2. Virtual Path (`virtual_path`)

```
/mnt/user-data/uploads/document.pdf
```

**Usage:**
- Path format observed and used by the Agent inside the sandbox environment.
- Automatically mapped to the physical location by the sandbox provider.
- All agent file operations and tools (`read_file`, `write_file`, `grep`, `glob`, `bash`) consume this path format.

**Example:**
Agent invocations during conversation:
```python
# Agent calls read_file tool
read_file(path="/mnt/user-data/uploads/document.pdf")

# Agent calls bash tool
bash(command="cat /mnt/user-data/uploads/document.pdf")
```

### 3. HTTP Access URL (`artifact_url`)

```
/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/document.pdf
```

**Usage:**
- Web frontend and HTTP clients use this URL to fetch artifacts.
- Used for downloading and previewing files in browsers.
- Serves content via authenticated HTTP streaming.

**Example:**
```typescript
// Frontend TypeScript/JavaScript code
const threadId = 'abc123';
const filename = 'document.pdf';

// Download file
const downloadUrl = `/api/threads/${threadId}/artifacts/mnt/user-data/uploads/${filename}?download=true`;
window.open(downloadUrl);

// Preview in new window
const viewUrl = `/api/threads/${threadId}/artifacts/mnt/user-data/uploads/${filename}`;
window.open(viewUrl, '_blank');

// Fetch via Fetch API
const response = await fetch(viewUrl);
const blob = await response.blob();
```

## Complete Workflow Example

### Scenario: Frontend uploads document for Agent analysis

```typescript
// 1. Frontend uploads file
async function uploadAndProcess(threadId: string, file: File) {
  // Upload multipart payload
  const formData = new FormData();
  formData.append('files', file);

  const uploadResponse = await fetch(
    `/api/threads/${threadId}/uploads`,
    {
      method: 'POST',
      body: formData
    }
  );

  const uploadData = await uploadResponse.json();
  const fileInfo = uploadData.files[0];

  console.log('File Metadata:', fileInfo);
  // {
  //   filename: "report.pdf",
  //   path: ".agent-workspace/threads/abc123/user-data/uploads/report.pdf",
  //   virtual_path: "/mnt/user-data/uploads/report.pdf",
  //   artifact_url: "/api/threads/abc123/artifacts/mnt/user-data/uploads/report.pdf",
  //   markdown_file: "report.md",
  //   markdown_path: ".agent-workspace/threads/abc123/user-data/uploads/report.md",
  //   markdown_virtual_path: "/mnt/user-data/uploads/report.md",
  //   markdown_artifact_url: "/api/threads/abc123/artifacts/mnt/user-data/uploads/report.md"
  // }

  // 2. Dispatch prompt to Agent
  await sendMessage(threadId, "Please analyze the uploaded PDF document");

  // Agent automatically sees the file context:
  // - report.pdf (Virtual path: /mnt/user-data/uploads/report.pdf)
  // - report.md (Virtual path: /mnt/user-data/uploads/report.md)

  // 3. Frontend can preview converted Markdown directly
  const mdResponse = await fetch(fileInfo.markdown_artifact_url);
  const markdownContent = await mdResponse.text();
  console.log('Markdown Content:', markdownContent);

  // 4. Or download original document
  const downloadLink = document.createElement('a');
  downloadLink.href = fileInfo.artifact_url + '?download=true';
  downloadLink.download = fileInfo.filename;
  downloadLink.click();
}
```

## Path Mapping Matrix

| Scenario | Path Representation | Example |
|---|---|---|
| Server Backend Direct Access | `path` | `.agent-workspace/threads/abc123/user-data/uploads/file.pdf` |
| Agent Tool Invocations | `virtual_path` | `/mnt/user-data/uploads/file.pdf` |
| Frontend Download / Preview | `artifact_url` | `/api/threads/abc123/artifacts/mnt/user-data/uploads/file.pdf` |
| Backup Scripts | `path` | `.agent-workspace/threads/abc123/user-data/uploads/file.pdf` |
| Server Logging & Auditing | `path` | `.agent-workspace/threads/abc123/user-data/uploads/file.pdf` |

## Code Examples

### Python - Backend Processing

```python
from pathlib import Path
from alpha.agents.middlewares.thread_data_middleware import THREAD_DATA_BASE_DIR

def process_uploaded_file(thread_id: str, filename: str):
    # Construct host filesystem path
    base_dir = Path.cwd() / THREAD_DATA_BASE_DIR / thread_id / "user-data" / "uploads"
    file_path = base_dir / filename

    # Read binary bytes
    with open(file_path, 'rb') as f:
        content = f.read()

    return content
```

### JavaScript - Frontend API Consumption

```javascript
// List uploaded files for thread
async function listUploadedFiles(threadId) {
  const response = await fetch(`/api/threads/${threadId}/uploads/list`);
  const data = await response.json();

  data.files.forEach(file => {
    console.log(`File: ${file.filename}`);
    console.log(`Download: ${file.artifact_url}?download=true`);
    console.log(`Preview: ${file.artifact_url}`);

    if (file.markdown_artifact_url) {
      console.log(`Markdown: ${file.markdown_artifact_url}`);
    }
  });

  return data.files;
}

// Delete uploaded file
async function deleteFile(threadId, filename) {
  const response = await fetch(
    `/api/threads/${threadId}/uploads/${filename}`,
    { method: 'DELETE' }
  );
  return response.json();
}
```

### React Component Example

```tsx
import React, { useState, useEffect } from 'react';

interface UploadedFile {
  filename: string;
  size: number;
  path: string;
  virtual_path: string;
  artifact_url: string;
  extension: string;
  modified: number;
  markdown_artifact_url?: string;
}

function FileUploadList({ threadId }: { threadId: string }) {
  const [files, setFiles] = useState<UploadedFile[]>([]);

  useEffect(() => {
    fetchFiles();
  }, [threadId]);

  async function fetchFiles() {
    const response = await fetch(`/api/threads/${threadId}/uploads/list`);
    const data = await response.json();
    setFiles(data.files);
  }

  async function handleUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const fileList = event.target.files;
    if (!fileList) return;

    const formData = new FormData();
    Array.from(fileList).forEach(file => {
      formData.append('files', file);
    });

    await fetch(`/api/threads/${threadId}/uploads`, {
      method: 'POST',
      body: formData
    });

    fetchFiles(); // Refresh list
  }

  async function handleDelete(filename: string) {
    await fetch(`/api/threads/${threadId}/uploads/${filename}`, {
      method: 'DELETE'
    });
    fetchFiles(); // Refresh list
  }

  return (
    <div>
      <input type="file" multiple onChange={handleUpload} />

      <ul>
        {files.map(file => (
          <li key={file.filename}>
            <span>{file.filename}</span>
            <a href={file.artifact_url} target="_blank" rel="noreferrer">Preview</a>
            <a href={`${file.artifact_url}?download=true`}>Download</a>
            {file.markdown_artifact_url && (
              <a href={file.markdown_artifact_url} target="_blank" rel="noreferrer">Markdown</a>
            )}
            <button onClick={() => handleDelete(file.filename)}>Delete</button>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

## Security & Architectural Notes

1. **Path Isolation & Security**
   - Actual host paths contain the thread ID, guaranteeing thread-level isolation.
   - The Gateway API strictly validates paths against traversal attacks (`../`).
   - Clients must never construct host filesystem paths directly; always use `artifact_url`.

2. **Agent Sandboxing**
   - The Agent only sees and interacts with virtual `/mnt/user-data/` paths.
   - The underlying sandbox automatically mounts the corresponding thread directory.
   - The Agent is completely decoupled from the host filesystem layout.

3. **Frontend Integration**
   - Always consume `artifact_url` for downloads and previews.
   - Append `?download=true` to force standard browser download behavior.

4. **Markdown Conversion**
   - When conversion is active and successful, `markdown_*` properties are populated.
   - Markdown representations are optimized for fast LLM reading and summarization.
   - The original binary document is always preserved intact.