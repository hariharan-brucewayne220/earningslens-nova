import { useState, useEffect, useRef } from 'react';
import { Download, Volume2 } from 'lucide-react';
import { SessionSetup } from './components/SessionSetup';
import { TranscriptPanel } from './components/TranscriptPanel';
import { VerificationFeed } from './components/VerificationFeed';
import { ConfidenceMeter } from './components/ConfidenceMeter';
import { MacroDashContext } from './components/MacroDashContext';
import { getClaims, getTranscript, endSession, getBriefing, getReport, prefetchSession } from './api';
import type { Claim, TranscriptSegment, ClaimsResponse, MacroData } from './types';

type Phase = 'setup' | 'active' | 'ended';

export default function App() {
  const [phase, setPhase] = useState<Phase>('setup');
  const [sessionId, setSessionId] = useState<string>('');
  const [ticker, setTicker] = useState<string>('NVDA');

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

  function handleSessionReady(sid: string, tick: string) {
    setSessionId(sid);
    setTicker(tick);
    setPhase('active');
  }

  // Start polling when session becomes active
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
      } catch {
        // Polling errors are non-fatal; backend may not be running
      }
    }

    poll();
    pollRef.current = setInterval(poll, 5000);

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [phase, sessionId]);

  // Load macro data after session starts
  useEffect(() => {
    if (phase !== 'active') return;
    prefetchSession(sessionId, ticker)
      .then((res) => {
        if (res.macro_data) setMacroData(res.macro_data as MacroData);
      })
      .catch(() => {});
  }, [phase, sessionId, ticker]);

  async function handleEndCall() {
    if (pollRef.current) clearInterval(pollRef.current);
    try {
      await endSession(sessionId);
    } catch {
      // proceed anyway
    }
    setPhase('ended');
    // Fetch briefing
    try {
      const b = await getBriefing(sessionId);
      if (b.status !== 'error') {
        setBriefing({ text: b.briefing_text, audioUrl: b.audio_url });
      }
    } catch {
      // briefing unavailable
    }
  }

  async function handleDownloadReport() {
    try {
      const report = await getReport(sessionId);
      const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `earningslens-${ticker}-${sessionId.slice(0, 8)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      // unavailable
    }
  }

  return (
    <div className="flex flex-col min-h-screen bg-gray-950 text-gray-100">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-gray-800 bg-gray-950 shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-lg font-semibold tracking-tight text-white">EarningsLens</span>
          {phase !== 'setup' && (
            <>
              <span className="text-gray-700">·</span>
              <span className="font-mono text-blue-400 text-sm">{ticker}</span>
              <span className="text-xs px-2 py-0.5 rounded-full bg-gray-800 text-gray-400 font-mono">
                {sessionId.slice(0, 8)}
              </span>
            </>
          )}
        </div>

        <div className="flex items-center gap-4">
          {phase === 'active' && (
            <button
              onClick={handleEndCall}
              className="text-xs bg-red-900 hover:bg-red-800 text-red-300 border border-red-700 rounded-lg px-3 py-1.5 transition-colors"
            >
              End Call
            </button>
          )}
          {phase === 'ended' && (
            <button
              onClick={handleDownloadReport}
              className="flex items-center gap-1.5 text-xs bg-gray-800 hover:bg-gray-700 text-gray-300 border border-gray-700 rounded-lg px-3 py-1.5 transition-colors"
            >
              <Download size={12} />
              Download Report
            </button>
          )}
          <div className="flex items-center gap-1.5 bg-gray-900 border border-gray-800 rounded-lg px-3 py-1.5">
            <span className="text-xs text-gray-500">Powered by</span>
            <span className="text-xs font-semibold text-orange-400">Amazon Nova</span>
          </div>
        </div>
      </header>

      {/* Phase 1: Setup */}
      {phase === 'setup' && (
        <main className="flex-1 px-6">
          <SessionSetup onSessionReady={handleSessionReady} />
        </main>
      )}

      {/* Phase 2: Active session */}
      {(phase === 'active' || phase === 'ended') && (
        <main className="flex-1 flex flex-col overflow-hidden">
          {/* Briefing banner (phase 3) */}
          {phase === 'ended' && briefing && (
            <div className="bg-gray-900 border-b border-gray-800 px-6 py-3 flex items-center gap-4 shrink-0">
              <div className="flex items-center gap-2 text-orange-400">
                <Volume2 size={16} />
                <span className="text-sm font-medium">Nova Sonic Briefing</span>
              </div>
              {briefing.audioUrl && (
                <audio controls className="h-8" src={briefing.audioUrl} />
              )}
              {briefing.text && (
                <p className="text-xs text-gray-400 truncate max-w-lg">{briefing.text}</p>
              )}
            </div>
          )}

          {/* Confidence meter row */}
          <div className="px-4 py-3 border-b border-gray-800 shrink-0">
            <ConfidenceMeter stats={claimStats} />
          </div>

          {/* Main split panels */}
          <div className="flex flex-1 overflow-hidden divide-x divide-gray-800">
            {/* Left: Transcript */}
            <div className="flex-1 overflow-hidden">
              <TranscriptPanel segments={segments} claims={claims} />
            </div>

            {/* Right: Verification feed */}
            <div className="flex-1 overflow-hidden">
              <VerificationFeed claims={claims} />
            </div>
          </div>

          {/* Bottom: Macro strip */}
          <MacroDashContext ticker={ticker} data={macroData} />
        </main>
      )}
    </div>
  );
}
