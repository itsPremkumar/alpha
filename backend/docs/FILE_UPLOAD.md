# File Upload Feature

## Overview

The Alpha backend provides comprehensive file upload capabilities, supporting multi-file uploads and optional automated conversion of Office documents and PDFs into Markdown.

## Feature Highlights

- ✅ Multi-file simultaneous uploads
- ✅ Optional document-to-Markdown conversion (PDF, PowerPoint, Excel, Word)
- ✅ Thread-isolated file storage directories
- ✅ Agent automatic awareness of files uploaded in the current message
- ✅ Endpoints for querying file listings and deleting files

## API Endpoints

### 1. Upload Files
```
POST /api/threads/{thread_id}/uploads
```

**Request Body:** `multipart/form-data`
- `files`: One or more files

The Gateway enforces application-layer limits on upload payloads: by default, a maximum of 10 files, 50 MiB per file, and 100 MiB total per request. These can be adjusted via `uploads.max_files`, `uploads.max_file_size`, and `uploads.max_total_size` in `config.yaml`. The frontend queries these exact limits to provide pre-selection validation, and the backend returns `413 Payload Too Large` when exceeded.

**Response:**
```json
{
  "success": true,
  "files": [
    {
      "filename": "document.pdf",
      "size": 1234567,
      "path": ".agent-workspace/threads/{thread_id}/user-data/uploads/document.pdf",
      "virtual_path": "/mnt/user-data/uploads/document.pdf",
      "artifact_url": "/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/document.pdf",
      "markdown_file": "document.md",
      "markdown_path": ".agent-workspace/threads/{thread_id}/user-data/uploads/document.md",
      "markdown_virtual_path": "/mnt/user-data/uploads/document.md",
      "markdown_artifact_url": "/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/document.md"
    }
  ],
  "message": "Successfully uploaded 1 file(s)"
}
```

**Path Explanations:**
- `path`: Actual filesystem path (relative to `backend/` directory)
- `virtual_path`: Virtual path inside the agent sandbox
- `artifact_url`: Frontend HTTP download / preview URL

### 2. Query Upload Limits
```
GET /api/threads/{thread_id}/uploads/limits
```

Returns the active Gateway upload constraints for frontend validation prior to file selection.

**Response:**
```json
{
  "max_files": 10,
  "max_file_size": 52428800,
  "max_total_size": 104857600
}
```

### 3. List Uploaded Files
```
GET /api/threads/{thread_id}/uploads/list
```

**Response:**
```json
{
  "files": [
    {
      "filename": "document.pdf",
      "size": 1234567,
      "path": ".agent-workspace/threads/{thread_id}/user-data/uploads/document.pdf",
      "virtual_path": "/mnt/user-data/uploads/document.pdf",
      "artifact_url": "/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/document.pdf",
      "extension": ".pdf",
      "modified": 1705997600.0
    }
  ],
  "count": 1
}
```

### 4. Delete File
```
DELETE /api/threads/{thread_id}/uploads/{filename}
```

**Response:**
```json
{
  "success": true,
  "message": "Deleted document.pdf"
}
```

## Supported Document Formats

When `uploads.auto_convert_documents: true` is explicitly enabled, the following formats are automatically converted to Markdown:
- PDF (`.pdf`)
- PowerPoint (`.ppt`, `.pptx`)
- Excel (`.xls`, `.xlsx`)
- Word (`.doc`, `.docx`)

Converted Markdown files are saved in the same directory, using the original filename with the `.md` extension.

By default, automatic conversion is disabled to avoid parsing untrusted Office/PDF uploads on the host machine. Only enable `uploads.auto_convert_documents: true` in trusted environments where this risk is acknowledged.

## Agent Integration

### File Context in Current Message

When sending a message, the frontend includes metadata for newly uploaded files in `HumanMessage.additional_kwargs.files`. `UploadsMiddleware` injects file context into the Agent's prompt for the current message:

```xml
<current_uploads>
The following files were uploaded in this message:

- document.pdf (1.2 MB)
  Path: /mnt/user-data/uploads/document.pdf

To work with these files:
- Read from the file first — use the outline line numbers and `read_file` to locate relevant sections.
- Use `grep` to search for keywords when you are not sure which section to look at.
- Use `glob` to find files by name pattern.
</current_uploads>
```

Files uploaded in earlier turns are not reinjected every turn. The Agent can query historical uploads via `list_uploaded_files` on demand (with optional `query` and `extensions` filtering prior to truncation). If the filename is known, the Agent can directly access files under `/mnt/user-data/uploads/` with `read_file` or `grep`.

### Accessing Uploaded Files

The Agent runs inside a sandbox using virtual paths. The Agent reads uploaded files using the `read_file` tool:

```python
# Read original PDF (if supported)
read_file(path="/mnt/user-data/uploads/document.pdf")

# Read converted Markdown (recommended)
read_file(path="/mnt/user-data/uploads/document.md")
```

**Path Relationships:**
- Agent usage: `/mnt/user-data/uploads/document.pdf` (Virtual Path)
- Physical storage: `backend/.agent-workspace/threads/{thread_id}/user-data/uploads/document.pdf`
- Frontend access: `/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/document.pdf` (HTTP URL)

The upload workflow follows a "thread directory first" strategy:
- Writes first to `backend/.agent-workspace/threads/{thread_id}/user-data/uploads/` as the authoritative store.
- Local sandbox (`sandbox_id=local`) uses the thread directory content directly.
- By default, non-local sandboxes synchronize uploaded files to `/mnt/user-data/uploads/*` upon acquisition via `acquire_async` to ensure runtime visibility.
- If Gateway and remote sandboxes share the same mounted storage (e.g. aligned PVC, NFS, or hostPath), set `sandbox.thread_data_mounts: true` to skip per-file synchronization.
- If unsure about mount topology, omit this setting to retain automatic detection. Setting it to `true` erroneously will result in files existing on the Gateway host while remaining invisible in the sandbox.

## Testing Examples

### Using curl

```bash
# 1. Upload single file
curl -X POST http://localhost:2026/api/threads/test-thread/uploads \
  -F "files=@/path/to/document.pdf"

# 2. Upload multiple files
curl -X POST http://localhost:2026/api/threads/test-thread/uploads \
  -F "files=@/path/to/document.pdf" \
  -F "files=@/path/to/presentation.pptx" \
  -F "files=@/path/to/spreadsheet.xlsx"

# 3. List uploaded files
curl http://localhost:2026/api/threads/test-thread/uploads/list

# 4. Delete file
curl -X DELETE http://localhost:2026/api/threads/test-thread/uploads/document.pdf
```

### Using Python

```python
import requests

thread_id = "test-thread"
base_url = "http://localhost:2026"

# Upload files
files = [
    ("files", open("document.pdf", "rb")),
    ("files", open("presentation.pptx", "rb")),
]
response = requests.post(
    f"{base_url}/api/threads/{thread_id}/uploads",
    files=files
)
print(response.json())

# List files
response = requests.get(f"{base_url}/api/threads/{thread_id}/uploads/list")
print(response.json())

# Delete file
response = requests.delete(
    f"{base_url}/api/threads/{thread_id}/uploads/document.pdf"
)
print(response.json())
```

## Storage Directory Structure

```
backend/.agent-workspace/threads/
└── {thread_id}/
    └── user-data/
        └── uploads/
            ├── document.pdf          # Original document
            ├── document.md           # Converted Markdown
            ├── presentation.pptx
            ├── presentation.md
            └── ...
```

## Constraints & Security

- Maximum file size: 100MB (configurable in `nginx.conf` via `client_max_body_size`)
- Filename sanitization: Paths are validated to prevent directory traversal
- Thread isolation: Uploads are partitioned strictly by thread ID
- Automatic document conversion is disabled by default; enable via `uploads.auto_convert_documents: true` in `config.yaml`

## Technical Architecture

### Components

1. **Upload Router** (`app/gateway/routers/uploads.py`)
   - Handles upload, listing, and deletion requests
   - Executes optional document conversion via markitdown

2. **Uploads Middleware** (`packages/harness/agent_workspace/agents/middlewares/uploads_middleware.py`)
   - Inspects `additional_kwargs.files` on the incoming message
   - Injects `<current_uploads>` context into the Agent's turn
   - Historical uploads queried on-demand via `list_uploaded_files`

3. **Nginx Configuration** (`nginx.conf`)
   - Routes upload requests to Gateway API
   - Sets body size constraints for uploads

### Dependencies

- `markitdown>=0.0.1a2` - Document conversion
- `python-multipart>=0.0.20` - Multipart form processing

## Troubleshooting

### Upload Fails

1. Check file size against configured limit.
2. Confirm Gateway API service is running.
3. Verify host disk space is sufficient.
4. Check Gateway logs: `make gateway`.

### Document Conversion Fails

1. Check if markitdown is installed: `uv run python -c "import markitdown"`.
2. Inspect server logs for extraction tracebacks.
3. Encrypted or malformed files may fail conversion; the original binary is still preserved.

### Agent Cannot See Uploaded Files

1. Confirm UploadsMiddleware is registered in `agent.py`.
2. Verify `thread_id` matches across requests.
3. Confirm file exists in `backend/.agent-workspace/threads/{thread_id}/user-data/uploads/`.
4. For non-local sandboxes, ensure upload route completes sandbox synchronization without errors.