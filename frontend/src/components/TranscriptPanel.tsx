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

function verdictUnderline(verdict: Claim['verdict']): string {
  if (verdict === 'VERIFIED') return 'underline decoration-green-500 decoration-2';
  if (verdict === 'FLAGGED') return 'underline decoration-red-500 decoration-2';
  return 'underline decoration-yellow-500 decoration-2';
}

function highlightSegment(text: string, claims: Claim[]): React.ReactNode {
  // Find any claim whose text partially matches this segment
  const matchedClaim = claims.find((c) => {
    const claimWords = c.claim_text.toLowerCase().split(' ').slice(0, 5).join(' ');
    return text.toLowerCase().includes(claimWords);
  });

  if (!matchedClaim) return text;

  return (
    <span className={verdictUnderline(matchedClaim.verdict)}>
      {text}
    </span>
  );
}

export function TranscriptPanel({ segments, claims }: TranscriptPanelProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [segments.length]);

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-800">
        <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
        <h2 className="text-sm font-medium text-gray-300 uppercase tracking-widest">
          Live Transcript
        </h2>
        <span className="ml-auto text-xs text-gray-600 font-mono">
          {segments.length} segments
        </span>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
        {segments.length === 0 ? (
          <div className="flex items-center justify-center h-full">
            <p className="text-gray-600 text-sm">Waiting for transcript…</p>
          </div>
        ) : (
          segments.map((seg, idx) => (
            <div key={idx} className="flex gap-3 group">
              <span className="text-xs text-gray-600 font-mono pt-0.5 shrink-0 w-10 text-right">
                {formatTime(seg.start_time)}
              </span>
              <p className="text-sm text-gray-300 leading-relaxed">
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
