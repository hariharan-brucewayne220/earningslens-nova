import { useState } from 'react';
import { Upload, Loader2, CheckCircle2, XCircle, Circle } from 'lucide-react';
import { startSession, prefetchSession, fetchFiling, uploadAudio } from '../api';

interface SessionSetupProps {
  onSessionReady: (sessionId: string, ticker: string) => void;
}

interface SetupStep {
  id: string;
  label: string;
  status: 'pending' | 'running' | 'done' | 'error';
  detail?: string;
}

export function SessionSetup({ onSessionReady }: SessionSetupProps) {
  const [ticker, setTicker] = useState('NVDA');
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [steps, setSteps] = useState<SetupStep[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function updateStep(id: string, patch: Partial<SetupStep>) {
    setSteps((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    const initialSteps: SetupStep[] = [
      { id: 'session', label: 'Creating session', status: 'running' },
      { id: 'prefetch', label: `Fetching ${ticker.toUpperCase()} market data`, status: 'pending' },
      { id: 'filing', label: `Loading SEC filing for ${ticker.toUpperCase()}`, status: 'pending' },
    ];
    if (audioFile) initialSteps.push({ id: 'audio', label: 'Uploading audio', status: 'pending' });
    setSteps(initialSteps);

    try {
      const { session_id } = await startSession(ticker.toUpperCase());
      updateStep('session', { status: 'done', detail: session_id.slice(0, 8) });

      updateStep('prefetch', { status: 'running' });
      const prefetch = await prefetchSession(session_id, ticker.toUpperCase());
      updateStep('prefetch', { status: 'done', detail: `${prefetch.cached_keys?.length ?? 0} keys cached` });

      updateStep('filing', { status: 'running' });
      const filing = await fetchFiling(ticker.toUpperCase());
      updateStep('filing', { status: 'done', detail: `${filing.form_type} · ${filing.filing_date}` });

      if (audioFile) {
        updateStep('audio', { status: 'running' });
        await uploadAudio(session_id, audioFile);
        updateStep('audio', { status: 'done', detail: audioFile.name });
      }

      onSessionReady(session_id, ticker.toUpperCase());
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Setup failed';
      setError(message);
      setSteps((prev) => prev.map((s) => (s.status === 'running' ? { ...s, status: 'error' } : s)));
    } finally {
      setLoading(false);
    }
  }

  function handleFileDrop(e: React.DragEvent) {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file) setAudioFile(file);
  }

  return (
    <div className="flex items-center justify-center min-h-[calc(100vh-56px)]">
      <div className="w-full max-w-md">
        {/* Hero text */}
        <div className="text-center mb-8">
          <div
            className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-medium mb-4"
            style={{
              background: 'rgba(249, 115, 22, 0.1)',
              border: '1px solid rgba(249, 115, 22, 0.2)',
              color: '#fb923c',
            }}
          >
            <span className="w-1.5 h-1.5 rounded-full bg-orange-400 pulse-dot" />
            Amazon Nova AI · Hackathon 2025
          </div>
          <h1 className="text-2xl font-bold text-white mb-2 tracking-tight">
            Real-Time Earnings Intelligence
          </h1>
          <p className="text-sm text-slate-500 leading-relaxed">
            Cross-references CEO claims against SEC filings, technical indicators,
            and macroeconomic data — simultaneously.
          </p>
        </div>

        {/* Card */}
        <div
          className="glass-card rounded-2xl p-6"
          style={{ boxShadow: '0 24px 48px rgba(0,0,0,0.4)' }}
        >
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Ticker */}
            <div>
              <label className="section-label block mb-2">Ticker Symbol</label>
              <input
                type="text"
                value={ticker}
                onChange={(e) => setTicker(e.target.value.toUpperCase())}
                placeholder="NVDA"
                maxLength={8}
                disabled={loading}
                className="w-full rounded-xl px-4 py-3 font-mono text-lg text-white placeholder-slate-600 focus:outline-none transition-all"
                style={{
                  background: 'rgba(255,255,255,0.04)',
                  border: '1px solid rgba(255,255,255,0.1)',
                }}
                onFocus={(e) => {
                  e.currentTarget.style.borderColor = 'rgba(249, 115, 22, 0.4)';
                  e.currentTarget.style.boxShadow = '0 0 0 3px rgba(249, 115, 22, 0.08)';
                }}
                onBlur={(e) => {
                  e.currentTarget.style.borderColor = 'rgba(255,255,255,0.1)';
                  e.currentTarget.style.boxShadow = 'none';
                }}
              />
            </div>

            {/* Audio upload */}
            <div>
              <label className="section-label block mb-2">
                Audio File <span className="normal-case" style={{ color: 'rgba(100,116,139,0.4)' }}>(optional)</span>
              </label>
              <div
                onDrop={handleFileDrop}
                onDragOver={(e) => e.preventDefault()}
                onClick={() => !loading && document.getElementById('audio-input')?.click()}
                className="rounded-xl p-5 text-center cursor-pointer transition-all duration-200"
                style={{
                  background: 'rgba(255,255,255,0.02)',
                  border: '1px dashed rgba(255,255,255,0.1)',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLDivElement).style.borderColor = 'rgba(249, 115, 22, 0.25)';
                  (e.currentTarget as HTMLDivElement).style.background = 'rgba(249, 115, 22, 0.03)';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLDivElement).style.borderColor = 'rgba(255,255,255,0.1)';
                  (e.currentTarget as HTMLDivElement).style.background = 'rgba(255,255,255,0.02)';
                }}
              >
                <input
                  id="audio-input"
                  type="file"
                  accept=".mp3,.wav,.m4a"
                  className="hidden"
                  onChange={(e) => setAudioFile(e.target.files?.[0] ?? null)}
                  disabled={loading}
                />
                {audioFile ? (
                  <div>
                    <p className="text-sm text-slate-300 font-mono">{audioFile.name}</p>
                    <p className="text-xs text-slate-600 mt-1">{(audioFile.size / 1024 / 1024).toFixed(1)} MB</p>
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-2 text-slate-600">
                    <Upload size={20} />
                    <p className="text-xs">Drop audio or click to browse</p>
                    <p className="section-label">.mp3 · .wav · .m4a</p>
                  </div>
                )}
              </div>
            </div>

            {/* Submit */}
            <button
              type="submit"
              disabled={loading || !ticker.trim()}
              className="w-full py-3 px-6 rounded-xl font-semibold text-sm transition-all duration-200 flex items-center justify-center gap-2"
              style={{
                background: loading || !ticker.trim()
                  ? 'rgba(255,255,255,0.05)'
                  : 'linear-gradient(135deg, #f97316, #ea580c)',
                color: loading || !ticker.trim() ? 'rgba(100,116,139,0.5)' : '#fff',
                border: 'none',
                cursor: loading || !ticker.trim() ? 'not-allowed' : 'pointer',
              }}
            >
              {loading ? (
                <>
                  <Loader2 size={15} className="animate-spin" />
                  Initializing…
                </>
              ) : (
                'Start Analysis'
              )}
            </button>
          </form>

          {/* Steps */}
          {steps.length > 0 && (
            <div
              className="mt-5 pt-5 space-y-2"
              style={{ borderTop: '1px solid rgba(255,255,255,0.06)' }}
            >
              {steps.map((step) => (
                <div key={step.id} className="flex items-start gap-3">
                  <StepIcon status={step.status} />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-slate-300">{step.label}</p>
                    {step.detail && (
                      <p className="section-label mt-0.5 truncate">{step.detail}</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Error */}
          {error && (
            <div
              className="mt-4 rounded-xl px-4 py-3"
              style={{
                background: 'rgba(244, 63, 94, 0.08)',
                border: '1px solid rgba(244, 63, 94, 0.2)',
              }}
            >
              <p className="text-xs text-rose-400">{error}</p>
            </div>
          )}
        </div>

        {/* Footer note */}
        <p className="text-center section-label mt-4">
          Nova Lite · Nova Embeddings · Nova Act · Nova Sonic
        </p>
      </div>
    </div>
  );
}

function StepIcon({ status }: { status: SetupStep['status'] }) {
  if (status === 'running') return <Loader2 size={13} className="animate-spin text-orange-400 shrink-0 mt-0.5" />;
  if (status === 'done') return <CheckCircle2 size={13} className="text-emerald-500 shrink-0 mt-0.5" />;
  if (status === 'error') return <XCircle size={13} className="text-rose-500 shrink-0 mt-0.5" />;
  return <Circle size={13} className="shrink-0 mt-0.5" style={{ color: 'rgba(100,116,139,0.3)' }} />;
}
