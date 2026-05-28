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

export interface StudySetLinkSource {
  linkText: string;
  obsidianPath: string;
}

export interface StudySetSource {
  file: TFile;
  name: string;
  path: string;
  sourceHash: string;
  links: StudySetLinkSource[];
}

export interface PreviewCard {
  index: number;
  marker_line: number;
  marker_kind: "card" | "reverse" | string;
  import_id: string | null;
  front_md: string;
  back_md: string;
  source_path: string;
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
  source_path: string | null;
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

export interface StudySetPreviewSourcePath {
  obsidian_path: string;
  source_path: string;
  link_text: string;
  card_count: number;
  deck_paths: string[];
}

export interface StudySetPreviewMissing {
  link_text: string;
  obsidian_path: string;
  source_path: string;
}

export interface StudySetPreview {
  name: string;
  source_path: string;
  source_hash: string;
  root_deck_path: string;
  action: "create" | "update";
  will_update: boolean;
  existing_study_set_id: number | null;
  has_errors: boolean;
  errors: string[];
  warnings?: string[];
  summary: {
    source_path_count: number;
    card_count: number;
    missing_count: number;
  };
  source_paths: StudySetPreviewSourcePath[];
  missing: StudySetPreviewMissing[];
}

export interface StudySetApplyResponse {
  status: string;
  action: "created" | "updated";
  study_set: {
    id: number;
    name: string;
    kind: "custom" | string;
    deck_ids: number[];
    source_paths: string[];
    source_root_deck_id: number | null;
    source_root_deck_path: string;
    decks: Array<{
      id: number;
      name: string;
      full_path: string;
    }>;
  };
}
