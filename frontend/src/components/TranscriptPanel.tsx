import { useEffect, useRef } from 'react';
import type { Claim, TranscriptSegment } from '../types';

interface TranscriptPanelProps {
  segments: TranscriptSegment[];
  claims: Claim[];
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function getVerdictStyle(verdict: Claim['verdict']): string {
  if (verdict === 'VERIFIED') return 'underline decoration-emerald-500/70 decoration-2 underline-offset-2';
  if (verdict === 'FLAGGED') return 'underline decoration-rose-500/70 decoration-2 underline-offset-2';
  return 'underline decoration-amber-500/70 decoration-2 underline-offset-2';
}

function highlightSegment(text: string, claims: Claim[]): React.ReactNode {
  const matched = claims.find((c) => {
    if (!c.claim_text) return false;
    const words = c.claim_text.toLowerCase().split(' ').slice(0, 5).join(' ');
    return text.toLowerCase().includes(words);
  });
  if (!matched) return <>{text}</>;
  return <span className={getVerdictStyle(matched.verdict)}>{text}</span>;
}

export function TranscriptPanel({ segments, claims }: TranscriptPanelProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [segments.length]);

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-white/[0.06] shrink-0">
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 pulse-dot" />
        <h2 className="section-label">Live Transcript</h2>
        {segments.length > 0 && (
          <span
            className="ml-auto text-xs font-mono"
            style={{ color: 'rgba(148,163,184,0.4)' }}
          >
            {segments.length} seg
          </span>
        )}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-3">
        {segments.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 opacity-40">
            <div className="w-8 h-8 rounded-full border border-slate-700 flex items-center justify-center">
              <div className="w-2 h-2 rounded-full bg-slate-600 pulse-dot" />
            </div>
            <p className="text-xs text-slate-500">Waiting for transcript…</p>
          </div>
        ) : (
          segments.map((seg, idx) => (
            <div key={idx} className="flex gap-3 group">
              <span
                className="text-xs font-mono pt-0.5 shrink-0 w-9 text-right tabular-nums"
                style={{ color: 'rgba(100, 116, 139, 0.5)' }}
              >
                {formatTime(seg.start_time)}
              </span>
              <p className="text-sm leading-relaxed text-slate-300">
                {highlightSegment(seg.text, claims)}
              </p>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
