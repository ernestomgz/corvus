import { requestUrl } from "obsidian";

import type {
  ApplyDecision,
  ApplyResponse,
  CorvusPluginSettings,
  NoteSyncSource,
  PreviewSession,
  StudySetApplyResponse,
  StudySetPreview,
  StudySetSource,
} from "./types";
import { buildMultipartBody } from "./multipart";

type HeaderValue = string | string[] | undefined;

function encodeUtf8(value: string): ArrayBuffer {
  const bytes = new TextEncoder().encode(value);
  const copy = new Uint8Array(bytes.byteLength);
  copy.set(bytes);
  return copy.buffer;
}

export class CorvusClient {
  private readonly baseUrl: string;

  private readonly cookieJar = new Map<string, string>();

  constructor(private readonly settings: CorvusPluginSettings) {
    this.baseUrl = settings.baseUrl.replace(/\/+$/, "");
  }

  async login(): Promise<void> {
    await this.requestJson("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({
        email: this.settings.username,
        password: this.settings.password,
      }),
      headers: {
        "Content-Type": "application/json",
      },
    });
  }

  async createPreview(source: NoteSyncSource): Promise<PreviewSession> {
    const noteManifest = source.files.map((file, index) => ({
      field: `note_${index}`,
      path: file.path,
      source_hash: file.sourceHash,
    }));
    const manifest = source.attachments.map((attachment) => ({
      field: attachment.field,
      path: attachment.path,
    }));
    const { body, boundary } = buildMultipartBody(
      [
        { name: "source_path", value: source.path },
        { name: "content", value: source.content },
        { name: "root_deck_path", value: this.settings.rootDeckPath },
        { name: "source_hash", value: source.sourceHash },
        { name: "note_manifest", value: JSON.stringify(noteManifest) },
        { name: "attachment_manifest", value: JSON.stringify(manifest) },
      ],
      [
        ...source.files.map((file, index) => ({
          name: `note_${index}`,
          filename: file.file.name,
          contentType: "text/markdown; charset=utf-8",
          data: encodeUtf8(file.content),
        })),
        ...source.attachments.map((attachment) => ({
          name: attachment.field,
          filename: attachment.fileName,
          contentType: attachment.contentType,
          data: attachment.data,
        })),
      ],
    );

    return await this.requestJson("/api/v1/obsidian/preview", {
      method: "POST",
      body,
      headers: {
        "Content-Type": `multipart/form-data; boundary=${boundary}`,
      },
    });
  }

  async getPreview(sessionId: string): Promise<PreviewSession> {
    return await this.requestJson(`/api/v1/obsidian/preview/${sessionId}`, {
      method: "GET",
    });
  }

  async applyPreview(sessionId: string, sourceHash: string, decisions: ApplyDecision[]): Promise<ApplyResponse> {
    return await this.requestJson(`/api/v1/obsidian/preview/${sessionId}/apply`, {
      method: "POST",
      body: JSON.stringify({
        source_hash: sourceHash,
        decisions,
      }),
      headers: {
        "Content-Type": "application/json",
      },
    });
  }

  async cancelPreview(sessionId: string): Promise<void> {
    await this.requestJson(`/api/v1/obsidian/preview/${sessionId}/cancel`, {
      method: "POST",
    });
  }

  async previewStudySet(source: StudySetSource): Promise<StudySetPreview> {
    return await this.requestJson("/api/v1/obsidian/study-set/preview", {
      method: "POST",
      body: JSON.stringify(this.studySetPayload(source)),
      headers: {
        "Content-Type": "application/json",
      },
    });
  }

  async applyStudySet(source: StudySetSource): Promise<StudySetApplyResponse> {
    return await this.requestJson("/api/v1/obsidian/study-set/apply", {
      method: "POST",
      body: JSON.stringify(this.studySetPayload(source)),
      headers: {
        "Content-Type": "application/json",
      },
    });
  }

  private studySetPayload(source: StudySetSource): Record<string, unknown> {
    return {
      name: source.name,
      source_path: source.path,
      source_hash: source.sourceHash,
      root_deck_path: this.settings.rootDeckPath,
      links: source.links.map((link) => ({
        link_text: link.linkText,
        obsidian_path: link.obsidianPath,
      })),
    };
  }

  private async requestJson<T>(
    path: string,
    options: {
      method: string;
      body?: string | ArrayBuffer;
      headers?: Record<string, string>;
    },
  ): Promise<T> {
    const headers = { ...(options.headers ?? {}) };
    const cookieHeader = this.cookieHeader();
    if (cookieHeader) {
      headers.Cookie = cookieHeader;
    }

    const response = await requestUrl({
      url: `${this.baseUrl}${path}`,
      method: options.method,
      body: options.body,
      headers,
      throw: false,
    });

    this.captureCookies(response.headers as Record<string, HeaderValue>);

    if (response.status < 200 || response.status >= 300) {
      throw new Error(this.extractError(response));
    }

    return response.json as T;
  }

  private cookieHeader(): string {
    return Array.from(this.cookieJar.entries())
      .map(([name, value]) => `${name}=${value}`)
      .join("; ");
  }

  private captureCookies(headers: Record<string, HeaderValue>): void {
    const headerValues: string[] = [];
    for (const key of ["set-cookie", "Set-Cookie"]) {
      const raw = headers[key];
      if (Array.isArray(raw)) {
        headerValues.push(...raw);
      } else if (typeof raw === "string" && raw.length > 0) {
        headerValues.push(raw);
      }
    }

    if (headerValues.length === 0) {
      return;
    }

    const combined = headerValues.join(", ");
    for (const cookieName of ["sessionid", "csrftoken"]) {
      const match = combined.match(new RegExp(`${cookieName}=([^;\\s,]+)`));
      if (match?.[1]) {
        this.cookieJar.set(cookieName, match[1]);
      }
    }
  }

  private extractError(response: { json: unknown; text: string; status: number }): string {
    if (response.json && typeof response.json === "object") {
      const maybeError = (response.json as { error?: unknown }).error;
      if (typeof maybeError === "string" && maybeError.trim().length > 0) {
        return maybeError;
      }
    }

    if (response.text?.trim()) {
      return response.text.trim();
    }

    return `Corvus request failed with status ${response.status}`;
  }
}
