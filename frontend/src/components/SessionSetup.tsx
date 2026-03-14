import { useState } from 'react';
import { Upload, Loader2 } from 'lucide-react';
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
    setSteps((prev) =>
      prev.map((s) => (s.id === id ? { ...s, ...patch } : s))
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    const initialSteps: SetupStep[] = [
      { id: 'session', label: 'Creating session', status: 'running' },
      { id: 'prefetch', label: `Prefetching ${ticker.toUpperCase()} market data`, status: 'pending' },
      { id: 'filing', label: `Fetching SEC filing for ${ticker.toUpperCase()}`, status: 'pending' },
    ];
    if (audioFile) {
      initialSteps.push({ id: 'audio', label: 'Uploading audio', status: 'pending' });
    }
    setSteps(initialSteps);

    try {
      // Step 1: Create session
      const { session_id } = await startSession();
      updateStep('session', { status: 'done', detail: session_id.slice(0, 8) + '…' });

      // Step 2: Prefetch
      updateStep('prefetch', { status: 'running' });
      const prefetch = await prefetchSession(session_id, ticker.toUpperCase());
      updateStep('prefetch', {
        status: 'done',
        detail: `${prefetch.cached_keys?.length ?? 0} keys cached`,
      });

      // Step 3: Fetch filing
      updateStep('filing', { status: 'running' });
      const filing = await fetchFiling(ticker.toUpperCase());
      updateStep('filing', {
        status: 'done',
        detail: `${filing.form_type} · ${filing.filing_date}`,
      });

      // Step 4: Upload audio (optional)
      if (audioFile) {
        updateStep('audio', { status: 'running' });
        await uploadAudio(session_id, audioFile);
        updateStep('audio', { status: 'done', detail: audioFile.name });
      }

      onSessionReady(session_id, ticker.toUpperCase());
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Setup failed';
      setError(message);
      setSteps((prev) =>
        prev.map((s) => (s.status === 'running' ? { ...s, status: 'error' } : s))
      );
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
    <div className="flex items-center justify-center min-h-[calc(100vh-64px)]">
      <div className="w-full max-w-lg">
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-8 shadow-2xl">
          <h2 className="text-xl font-semibold text-gray-100 mb-1">New Analysis Session</h2>
          <p className="text-sm text-gray-500 mb-6">
            Configure the ticker and optionally upload an earnings call audio file.
          </p>

          <form onSubmit={handleSubmit} className="space-y-5">
            {/* Ticker input */}
            <div>
              <label className="block text-xs font-medium text-gray-400 uppercase tracking-widest mb-2">
                Ticker Symbol
              </label>
              <input
                type="text"
                value={ticker}
                onChange={(e) => setTicker(e.target.value.toUpperCase())}
                placeholder="NVDA"
                maxLength={8}
                className="w-full bg-gray-950 border border-gray-700 rounded-lg px-4 py-3 text-gray-100 font-mono text-lg placeholder-gray-600 focus:outline-none focus:border-blue-500 transition-colors"
                disabled={loading}
              />
            </div>

            {/* Audio upload */}
            <div>
              <label className="block text-xs font-medium text-gray-400 uppercase tracking-widest mb-2">
                Audio File <span className="text-gray-600 normal-case">(optional)</span>
              </label>
              <div
                onDrop={handleFileDrop}
                onDragOver={(e) => e.preventDefault()}
                className="border border-dashed border-gray-700 rounded-lg p-6 text-center cursor-pointer hover:border-gray-500 transition-colors"
                onClick={() => !loading && document.getElementById('audio-input')?.click()}
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
                    <p className="text-sm text-gray-300 font-mono">{audioFile.name}</p>
                    <p className="text-xs text-gray-500 mt-1">
                      {(audioFile.size / 1024 / 1024).toFixed(1)} MB
                    </p>
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-2 text-gray-500">
                    <Upload size={24} />
                    <p className="text-sm">Drop audio file here or click to browse</p>
                    <p className="text-xs">.mp3, .wav, .m4a</p>
                  </div>
                )}
              </div>
            </div>

            {/* Submit button */}
            <button
              type="submit"
              disabled={loading || !ticker.trim()}
              className="w-full bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white font-medium rounded-lg py-3 px-6 transition-colors flex items-center justify-center gap-2"
            >
              {loading ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  Setting up…
                </>
              ) : (
                'Start Analysis'
              )}
            </button>
          </form>

          {/* Steps progress */}
          {steps.length > 0 && (
            <div className="mt-6 border-t border-gray-800 pt-5 space-y-2">
              {steps.map((step) => (
                <div key={step.id} className="flex items-center gap-3">
                  <StepIcon status={step.status} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-gray-300">{step.label}</p>
                    {step.detail && (
                      <p className="text-xs text-gray-500 font-mono truncate">{step.detail}</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="mt-4 bg-red-950 border border-red-800 rounded-lg px-4 py-3">
              <p className="text-sm text-red-400">{error}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function StepIcon({ status }: { status: SetupStep['status'] }) {
  if (status === 'running') return <Loader2 size={14} className="animate-spin text-blue-400 shrink-0" />;
  if (status === 'done') return <span className="text-green-500 text-sm shrink-0">✓</span>;
  if (status === 'error') return <span className="text-red-500 text-sm shrink-0">✗</span>;
  return <span className="w-3.5 h-3.5 rounded-full border border-gray-600 shrink-0 inline-block" />;
}
