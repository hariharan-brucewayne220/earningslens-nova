import { useState, useEffect, useRef } from 'react';
import { Download, Volume2, Play } from 'lucide-react';
import { SonicDemo } from './pages/SonicDemo';
import { SessionSetup } from './components/SessionSetup';
import { TranscriptPanel } from './components/TranscriptPanel';
import { VerificationFeed } from './components/VerificationFeed';
import { ConfidenceMeter } from './components/ConfidenceMeter';
import { MacroDashContext } from './components/MacroDashContext';
import { getClaims, getTranscript, processSession, endSession, getBriefing, getPdfReport, prefetchSession } from './api';
import type { Claim, TranscriptSegment, ClaimsResponse, MacroData } from './types';

type Phase = 'setup' | 'active' | 'ended';

export default function App() {
  // Standalone Sonic demo at /sonic
  if (window.location.pathname === '/sonic') {
    return <SonicDemo onBack={() => { window.location.pathname = '/'; }} />;
  }


  const [phase, setPhase] = useState<Phase>(() => (localStorage.getItem('el_phase') as Phase) || 'setup');
  const [sessionId, setSessionId] = useState<string>(() => localStorage.getItem('el_session') || '');
  const [ticker, setTicker] = useState<string>(() => localStorage.getItem('el_ticker') || 'NVDA');

  const [segments, setSegments] = useState<TranscriptSegment[]>([]);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [claimStats, setClaimStats] = useState<Pick<ClaimsResponse, 'total' | 'verified' | 'flagged' | 'unverifiable'>>({
    total: 0,
    verified: 0,
    flagged: 0,
    unverifiable: 0,
  });
  const [macroData, setMacroData] = useState<MacroData>({});
  const [briefing, setBriefing] = useState<{ text: string; audioUrl: string } | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const processedRef = useRef(false);

  function handleSessionReady(sid: string, tick: string) {
    setSessionId(sid);
    setTicker(tick);
    processedRef.current = false;
    setPhase('active');
    localStorage.setItem('el_session', sid);
    localStorage.setItem('el_ticker', tick);
    localStorage.setItem('el_phase', 'active');
  }

  useEffect(() => {
    if (phase !== 'active') return;

    async function poll() {
      try {
        const [claimsRes, transcriptRes] = await Promise.all([
          getClaims(sessionId),
          getTranscript(sessionId),
        ]);

        setClaims(claimsRes.claims ?? []);
        setClaimStats({
          total: claimsRes.total,
          verified: claimsRes.verified,
          flagged: claimsRes.flagged,
          unverifiable: claimsRes.unverifiable,
        });

        if (transcriptRes.segments?.length) {
          setSegments(transcriptRes.segments);
        } else if (transcriptRes.transcript_text) {
          setSegments([{ text: transcriptRes.transcript_text, start_time: 0, end_time: 0 }]);
        }

        if (transcriptRes.status === 'COMPLETED' && transcriptRes.transcript_text && !processedRef.current) {
          processedRef.current = true;
          processSession(sessionId, ticker, transcriptRes.transcript_text)
            .then((res) => {
              const flat = (res.claims ?? []).map((c: any) => ({
                claim_text: c.claim?.claim_text ?? c.claim_text ?? '',
                metric: c.claim?.metric ?? c.metric ?? '',
                value: c.claim?.value ?? c.value ?? '',
                verdict: c.verdict ?? 'UNVERIFIABLE',
                confidence: c.confidence ?? 0,
                filing_match: c.filing_match ?? null,
                filing_delta: c.filing_delta ?? null,
                technical_context: c.technical_context ?? '',
                macro_context: c.macro_context ?? '',
                explanation: c.explanation ?? '',
              }));
              setClaims(flat);
              setClaimStats({
                total: flat.length,
                verified: flat.filter((c: any) => c.verdict === 'VERIFIED').length,
                flagged: flat.filter((c: any) => c.verdict === 'FLAGGED').length,
                unverifiable: flat.filter((c: any) => c.verdict === 'UNVERIFIABLE').length,
              });
            })
            .catch(() => { processedRef.current = false; });
        }
      } catch {
        // Polling errors are non-fatal
      }
    }

    poll();
    pollRef.current = setInterval(poll, 5000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [phase, sessionId]);

  useEffect(() => {
    if ((phase !== 'active' && phase !== 'ended') || !sessionId || !ticker) return;
    prefetchSession(sessionId, ticker)
      .then((res) => {
        if (res.macro_data) setMacroData(res.macro_data as MacroData);
      })
      .catch(() => {});
  }, [phase, sessionId, ticker]);

  async function downloadPdfReport() {
    const blob = await getPdfReport(sessionId);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `earningslens-${ticker}-${sessionId.slice(0, 8)}.pdf`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function handlePlayBriefingAudio() {
    if (!briefing?.audioUrl) return;
    const audio = new Audio(briefing.audioUrl);
    void audio.play().catch(() => {
      window.open(briefing.audioUrl, '_blank', 'noopener,noreferrer');
    });
  }

  async function handleEndCall() {
    if (pollRef.current) clearInterval(pollRef.current);
    try { await endSession(sessionId, ticker); } catch { /* proceed */ }
    setPhase('ended');
    localStorage.setItem('el_phase', 'ended');
    try {
      const b = await getBriefing(sessionId);
      if (b.status !== 'error') {
        setBriefing({ text: b.briefing_text, audioUrl: b.audio_url });
      }
    } catch { /* briefing unavailable */ }
    try {
      await downloadPdfReport();
    } catch { /* report unavailable */ }
  }

  async function handleDownloadReport() {
    try {
      await downloadPdfReport();
    } catch { /* unavailable */ }
  }

  return (
    <div className="flex h-screen overflow-hidden flex-col bg-app text-slate-200">
      {/* Header */}
      <header
        className="flex items-center justify-between px-5 py-3 shrink-0"
        style={{
          background: 'rgba(6, 13, 31, 0.9)',
          borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
          backdropFilter: 'blur(12px)',
        }}
      >
        <div className="flex items-center gap-3">
          {/* Logo mark */}
          <div
            className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0"
            style={{ background: 'linear-gradient(135deg, #f97316, #ea580c)' }}
          >
            <span className="text-white font-bold text-xs">EL</span>
          </div>
          <span className="text-sm font-semibold text-white tracking-tight">EarningsLens</span>

          {phase !== 'setup' && (
            <>
              <span style={{ color: 'rgba(255,255,255,0.15)' }}>·</span>
              <span className="font-mono text-orange-400 text-sm font-semibold">{ticker}</span>
              <span
                className="text-xs px-2 py-0.5 rounded font-mono"
                style={{
                  background: 'rgba(255,255,255,0.05)',
                  color: 'rgba(148,163,184,0.5)',
                  border: '1px solid rgba(255,255,255,0.07)',
                }}
              >
                {sessionId.slice(0, 8)}
              </span>
            </>
          )}
        </div>

        <div className="flex items-center gap-3">
          {phase === 'active' && (
            <button
              onClick={handleEndCall}
              className="text-xs px-3 py-1.5 rounded-lg font-medium transition-all duration-150"
              style={{
                background: 'rgba(244, 63, 94, 0.12)',
                color: '#f43f5e',
                border: '1px solid rgba(244, 63, 94, 0.25)',
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLButtonElement).style.background = 'rgba(244, 63, 94, 0.2)';
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLButtonElement).style.background = 'rgba(244, 63, 94, 0.12)';
              }}
            >
              End Call
            </button>
          )}
          {phase === 'ended' && (
            <button
              onClick={handleDownloadReport}
              className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg font-medium transition-all duration-150"
              style={{
                background: 'rgba(255,255,255,0.05)',
                color: 'rgba(148,163,184,0.8)',
                border: '1px solid rgba(255,255,255,0.08)',
              }}
            >
              <Download size={11} />
              PDF Report
            </button>
          )}

          {/* Nova badge */}
          <div
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg"
            style={{
              background: 'rgba(249, 115, 22, 0.08)',
              border: '1px solid rgba(249, 115, 22, 0.2)',
            }}
          >
            <span className="text-xs text-slate-500">Powered by</span>
            <span className="text-xs font-bold text-orange-400">Amazon Nova</span>
          </div>
        </div>
      </header>

      {/* Phase 1: Setup */}
      {phase === 'setup' && (
        <main className="flex-1 px-6">
          <SessionSetup onSessionReady={handleSessionReady} />
        </main>
      )}

      {/* Phase 2 & 3: Active / Ended */}
      {(phase === 'active' || phase === 'ended') && (
        <main className="flex-1 min-h-0 flex flex-col overflow-hidden">
          {/* Nova Sonic Briefing Banner */}
          {phase === 'ended' && briefing && (
            <div
              className="shrink-0 px-5 py-4"
              style={{
                background: 'rgba(249, 115, 22, 0.06)',
                borderBottom: '1px solid rgba(249, 115, 22, 0.15)',
              }}
            >
              <div
                className="rounded-2xl border px-4 py-4 flex items-start gap-4"
                style={{
                  background: 'rgba(255,255,255,0.03)',
                  borderColor: 'rgba(249, 115, 22, 0.12)',
                }}
              >
                <div className="flex items-center gap-2 shrink-0 pt-0.5">
                  <div
                    className="w-8 h-8 rounded-lg flex items-center justify-center"
                    style={{ background: 'rgba(249, 115, 22, 0.15)' }}
                  >
                    <Volume2 size={14} className="text-orange-400" />
                  </div>
                  <div>
                    <span className="text-xs font-semibold text-orange-400 block">Nova Briefing</span>
                    <span className="text-[11px] text-slate-500 block mt-0.5">Summary ready</span>
                  </div>
                </div>

                <div className="flex-1 min-w-0">
                  {briefing.text && (
                    <p className="text-sm text-slate-300 leading-6 whitespace-normal break-words">
                      {briefing.text}
                    </p>
                  )}
                </div>

                {briefing.audioUrl && (
                  <button
                    onClick={handlePlayBriefingAudio}
                    className="shrink-0 inline-flex items-center gap-2 rounded-xl px-3 py-2 text-xs font-medium transition-colors"
                    style={{
                      background: 'rgba(249, 115, 22, 0.12)',
                      color: '#fb923c',
                      border: '1px solid rgba(249, 115, 22, 0.18)',
                    }}
                  >
                    <Play size={12} />
                    Voice
                  </button>
                )}
              </div>
            </div>
          )}

          {/* Confidence meter row */}
          <div
            className="px-5 py-3 shrink-0"
            style={{ borderBottom: '1px solid rgba(255,255,255,0.06)' }}
          >
            <ConfidenceMeter stats={claimStats} />
          </div>

          {/* Main split panels */}
          <div className="flex flex-1 min-h-0 overflow-hidden" style={{ borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
            <div className="flex-1 min-h-0 overflow-hidden" style={{ borderRight: '1px solid rgba(255,255,255,0.06)' }}>
              <TranscriptPanel segments={segments} claims={claims} />
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
              <VerificationFeed claims={claims} />
            </div>
          </div>

          {/* Bottom macro strip */}
          <MacroDashContext ticker={ticker} data={macroData} />
        </main>
      )}
    </div>
  );
}
