import { requestUrl } from "obsidian";

import type {
  ApplyDecision,
  ApplyResponse,
  CorvusPluginSettings,
  NoteSyncAudit,
  NoteSyncSource,
  PreviewSession,
  StudySetApplyResponse,
  StudySetPreview,
  StudySetSource,
} from "./types";
import { buildMultipartBody } from "./multipart";

type HeaderValue = string | string[] | undefined;

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
    const preview = await this.sendNote<PreviewSession>("/api/v1/obsidian/preview", source);
    for (const card of preview.cards) {
      if (card.marker_column != null && source.originalMarkerColumn) {
        card.marker_column = source.originalMarkerColumn(card.marker_line, card.marker_column);
      }
      card.marker_line = source.originalLineNumbers?.[card.marker_line - 1] ?? card.marker_line;
    }
    return preview;
  }

  async auditNote(source: NoteSyncSource): Promise<NoteSyncAudit> {
    try {
      const audit = await this.sendNote<NoteSyncAudit>("/api/v1/obsidian/audit-note", source);
      if (audit.error_locations?.length) {
        audit.errors = audit.error_locations.flatMap(({ marker_line, errors }) => {
          const line = source.originalLineNumbers?.[marker_line - 1] ?? marker_line;
          return errors.map((error) => `Line ${line} · ${error}`);
        });
      }
      return audit;
    } catch (error) {
      if (!(error instanceof Error)) throw error;
      throw new Error(`${error.message} (note: ${source.path})`);
    }
  }

  private async sendNote<T>(path: string, source: NoteSyncSource): Promise<T> {
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
        { name: "attachment_manifest", value: JSON.stringify(manifest) },
      ],
      source.attachments.map((attachment) => ({
        name: attachment.field,
        filename: attachment.fileName,
        contentType: attachment.contentType,
        data: attachment.data,
      })),
    );

    return await this.requestJson(path, {
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
        note_type: link.noteType,
        note_type_explicit: link.noteTypeExplicit,
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

    const status = `${options.method} ${path} returned HTTP ${response.status}`;
    let payload: unknown;
    try {
      payload = JSON.parse(response.text);
    } catch {
      const format = response.text.trimStart().startsWith("<") ? "HTML" : "a non-JSON response";
      throw new Error(`${status} (${format}). Check the Corvus server or proxy logs.`);
    }

    if (response.status < 200 || response.status >= 300) {
      const detail = (payload as { error?: unknown } | null)?.error;
      throw new Error(typeof detail === "string" && detail.trim() ? `${status}: ${detail}` : status);
    }

    return payload as T;
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
}
