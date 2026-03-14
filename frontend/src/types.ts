export interface Session {
  session_id: string;
  ticker: string;
  status: string;
}

export interface Claim {
  claim_text: string;
  metric: string;
  value: string;
  verdict: 'VERIFIED' | 'FLAGGED' | 'UNVERIFIABLE';
  confidence: number;
  filing_match: string | null;
  filing_delta: string | null;
  technical_context: string;
  macro_context: string;
  explanation: string;
}

export interface TranscriptSegment {
  text: string;
  start_time: number;
  end_time: number;
}

export interface ClaimsResponse {
  claims: Claim[];
  total: number;
  verified: number;
  flagged: number;
  unverifiable: number;
}

export interface TranscriptResponse {
  status: string;
  transcript_text: string;
  segments: TranscriptSegment[];
}

export interface FilingFetchResponse {
  s3_path: string;
  form_type: string;
  filing_date: string;
  status: string;
}

export interface EmbedJobResponse {
  job_id: string;
}

export interface EmbedProgressResponse {
  progress_pct: number;
  status: string;
}

export interface PrefetchResponse {
  cached_keys: string[];
  status: string;
  macro_data?: Record<string, unknown>;
}

export interface BriefingResponse {
  briefing_text: string;
  audio_url: string;
  status: string;
}

export interface MacroData {
  rsi?: number;
  macd?: number;
  macd_signal?: number;
  gdp_growth?: number;
  pce?: number;
  price?: number;
  change_pct?: number;
}
