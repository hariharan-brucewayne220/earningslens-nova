import axios from 'axios';
import type {
  ClaimsResponse,
  TranscriptResponse,
  FilingFetchResponse,
  EmbedJobResponse,
  EmbedProgressResponse,
  PrefetchResponse,
  BriefingResponse,
} from './types';

const client = axios.create({
  baseURL: '/api',
  timeout: 30000,
});

export async function startSession(): Promise<{ session_id: string }> {
  const res = await client.post('/session/start');
  return res.data as { session_id: string };
}

export async function uploadAudio(
  sessionId: string,
  file: File
): Promise<{ session_id: string; s3_uri: string; transcribe_job_name: string; status: string }> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await client.post(`/session/${sessionId}/upload-audio`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  });
  return res.data;
}

export async function prefetchSession(
  sessionId: string,
  ticker: string
): Promise<PrefetchResponse> {
  const res = await client.post(`/session/${sessionId}/prefetch`, { ticker });
  return res.data as PrefetchResponse;
}

export async function getTranscript(sessionId: string): Promise<TranscriptResponse> {
  const res = await client.get(`/session/${sessionId}/transcript`);
  return res.data as TranscriptResponse;
}

export async function processSession(
  sessionId: string,
  ticker: string,
  transcript: string
): Promise<{ claims: ClaimsResponse['claims'] }> {
  const res = await client.post(`/session/${sessionId}/process`, { ticker, transcript });
  return res.data;
}

export async function getClaims(sessionId: string): Promise<ClaimsResponse> {
  const res = await client.get(`/session/${sessionId}/claims`);
  return res.data as ClaimsResponse;
}

export async function endSession(sessionId: string): Promise<unknown> {
  const res = await client.post(`/session/${sessionId}/end`);
  return res.data;
}

export async function getBriefing(sessionId: string): Promise<BriefingResponse> {
  const res = await client.get(`/session/${sessionId}/briefing`);
  return res.data as BriefingResponse;
}

export async function getReport(sessionId: string): Promise<unknown> {
  const res = await client.get(`/session/${sessionId}/report.json`);
  return res.data;
}

export async function fetchFiling(ticker: string): Promise<FilingFetchResponse> {
  const res = await client.post('/filing/fetch', { ticker });
  return res.data as FilingFetchResponse;
}

export async function embedFiling(
  ticker: string,
  localPath: string
): Promise<EmbedJobResponse> {
  const res = await client.post('/filing/embed', { ticker, local_path: localPath });
  return res.data as EmbedJobResponse;
}

export async function getEmbedProgress(jobId: string): Promise<EmbedProgressResponse> {
  const res = await client.get(`/filing/embed/${jobId}`);
  return res.data as EmbedProgressResponse;
}

export function openClaimsStream(
  sessionId: string,
  onClaim: (data: string) => void,
  onError?: (err: Event) => void
): EventSource {
  const es = new EventSource(`/api/session/${sessionId}/stream`);
  es.onmessage = (event) => onClaim(event.data as string);
  if (onError) es.onerror = onError;
  return es;
}
