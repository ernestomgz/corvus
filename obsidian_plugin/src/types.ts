import type { TFile } from "obsidian";

export interface CorvusPluginSettings {
  baseUrl: string;
  username: string;
  password: string;
  rootDeckPath: string;
}

export const DEFAULT_SETTINGS: CorvusPluginSettings = {
  baseUrl: "http://localhost:8000",
  username: "",
  password: "",
  rootDeckPath: "",
};

export interface NoteAttachment {
  field: string;
  path: string;
  fileName: string;
  contentType: string;
  data: ArrayBuffer;
}

export interface NoteSyncSource {
  file: TFile;
  path: string;
  content: string;
  sourceHash: string;
  attachments: NoteAttachment[];
}

export interface PreviewCard {
  index: number;
  marker_line: number;
  marker_kind: "card" | "reverse" | string;
  import_id: string | null;
  front_md: string;
  back_md: string;
  existing: boolean;
  existing_card_id: string | null;
  has_changes: boolean;
  unchanged: boolean;
  warnings: string[];
  errors: string[];
  target_deck_path: string;
  deck_path: string[];
  will_write_back_id: boolean;
}

export interface PreviewSession {
  session_id: string;
  status: string;
  source_name: string;
  source_hash: string;
  source_path: string;
  root_deck_path: string;
  summary: {
    creates?: number;
    updates?: number;
    unchanged?: number;
    conflicts?: number;
    media_copied?: number;
    has_errors?: boolean;
  };
  planned_decks: string[];
  has_errors: boolean;
  cards: PreviewCard[];
}

export interface ApplyDecision {
  index: number;
  action: "apply" | "skip";
}

export interface ApplyCardResult {
  index: number;
  status: "created" | "updated" | "skipped" | "unchanged" | string;
  import_id: string | null;
  card_id: string | null;
  marker_line: number | null;
  marker_kind: "card" | "reverse" | string | null;
}

export interface ApplyResponse {
  session_id: string;
  status: string;
  summary: {
    created: number;
    updated: number;
    skipped: number;
    failed: number;
    decks_created: number;
    media_copied?: number;
  };
  cards: ApplyCardResult[];
}
