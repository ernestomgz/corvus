interface MultipartField {
  name: string;
  value: string;
}

interface MultipartFile {
  name: string;
  filename: string;
  contentType: string;
  data: ArrayBuffer;
}

function encode(value: string): Uint8Array {
  return new TextEncoder().encode(value);
}

function concat(chunks: Uint8Array[]): ArrayBuffer {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.length;
  }
  return merged.buffer;
}

export function buildMultipartBody(
  fields: MultipartField[],
  files: MultipartFile[],
): { body: ArrayBuffer; boundary: string } {
  const boundary = `----corvus-${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
  const chunks: Uint8Array[] = [];

  for (const field of fields) {
    chunks.push(encode(`--${boundary}\r\n`));
    chunks.push(
      encode(`Content-Disposition: form-data; name="${field.name}"\r\n\r\n${field.value}\r\n`),
    );
  }

  for (const file of files) {
    chunks.push(encode(`--${boundary}\r\n`));
    chunks.push(
      encode(
        `Content-Disposition: form-data; name="${file.name}"; filename="${file.filename}"\r\n` +
          `Content-Type: ${file.contentType}\r\n\r\n`,
      ),
    );
    chunks.push(new Uint8Array(file.data));
    chunks.push(encode("\r\n"));
  }

  chunks.push(encode(`--${boundary}--\r\n`));
  return { body: concat(chunks), boundary };
}
