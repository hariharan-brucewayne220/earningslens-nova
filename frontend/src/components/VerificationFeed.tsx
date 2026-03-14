import { useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import type { Claim } from '../types';

interface VerificationFeedProps {
  claims: Claim[];
}

const VERDICT_STYLES: Record<Claim['verdict'], { badge: string; border: string; bg: string }> = {
  VERIFIED: {
    badge: 'bg-green-900 text-green-400 border border-green-700',
    border: 'border-l-green-500',
    bg: 'hover:bg-green-950/20',
  },
  FLAGGED: {
    badge: 'bg-red-900 text-red-400 border border-red-700',
    border: 'border-l-red-500',
    bg: 'hover:bg-red-950/20',
  },
  UNVERIFIABLE: {
    badge: 'bg-yellow-900 text-yellow-400 border border-yellow-700',
    border: 'border-l-yellow-500',
    bg: 'hover:bg-yellow-950/20',
  },
};

function ClaimCard({ claim, isNew }: { claim: Claim; isNew: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const styles = VERDICT_STYLES[claim.verdict];

  return (
    <div
      className={`bg-gray-900 border border-gray-800 border-l-2 ${styles.border} ${styles.bg} rounded-lg overflow-hidden transition-colors ${isNew ? 'claim-card-enter' : ''}`}
    >
      <div
        className="px-4 py-3 cursor-pointer"
        onClick={() => setExpanded((v) => !v)}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <p className="text-sm text-gray-200 line-clamp-2 leading-snug">
              {claim.claim_text}
            </p>
            <div className="flex items-center gap-2 mt-2">
              {claim.metric && (
                <span className="text-xs font-mono text-gray-500">
                  {claim.metric}
                </span>
              )}
              {claim.value && (
                <span className="text-xs font-mono text-gray-400 bg-gray-800 px-1.5 py-0.5 rounded">
                  {claim.value}
                </span>
              )}
            </div>
          </div>
          <div className="flex flex-col items-end gap-1.5 shrink-0">
            <span className={`text-xs font-medium px-2 py-0.5 rounded ${styles.badge}`}>
              {claim.verdict}
            </span>
            <span className="text-xs text-gray-500 font-mono">
              {Math.round(claim.confidence * 100)}%
            </span>
          </div>
        </div>

        <div className="flex items-center justify-end mt-1">
          {expanded ? (
            <ChevronUp size={12} className="text-gray-600" />
          ) : (
            <ChevronDown size={12} className="text-gray-600" />
          )}
        </div>
      </div>

      {expanded && (
        <div className="border-t border-gray-800 px-4 py-3 space-y-3 text-xs">
          {claim.filing_match && (
            <div>
              <p className="text-gray-500 uppercase tracking-widest mb-1">Filing Match</p>
              <p className="text-gray-300 leading-relaxed">{claim.filing_match}</p>
            </div>
          )}
          {claim.filing_delta && (
            <div>
              <p className="text-gray-500 uppercase tracking-widest mb-1">Delta</p>
              <p className="text-gray-300 font-mono">{claim.filing_delta}</p>
            </div>
          )}
          {claim.technical_context && (
            <div>
              <p className="text-gray-500 uppercase tracking-widest mb-1">Technical Context</p>
              <p className="text-gray-400 leading-relaxed">{claim.technical_context}</p>
            </div>
          )}
          {claim.macro_context && (
            <div>
              <p className="text-gray-500 uppercase tracking-widest mb-1">Macro Context</p>
              <p className="text-gray-400 leading-relaxed">{claim.macro_context}</p>
            </div>
          )}
          {claim.explanation && (
            <div>
              <p className="text-gray-500 uppercase tracking-widest mb-1">Explanation</p>
              <p className="text-gray-300 leading-relaxed">{claim.explanation}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function VerificationFeed({ claims }: VerificationFeedProps) {
  // Newest first
  const ordered = [...claims].reverse();

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-800">
        <h2 className="text-sm font-medium text-gray-300 uppercase tracking-widest">
          Verification Feed
        </h2>
        <span className="ml-auto text-xs text-gray-600 font-mono">
          {claims.length} claims
        </span>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
        {ordered.length === 0 ? (
          <div className="flex items-center justify-center h-full">
            <p className="text-gray-600 text-sm">No claims verified yet…</p>
          </div>
        ) : (
          ordered.map((claim, idx) => (
            <ClaimCard
              key={`${claim.claim_text.slice(0, 20)}-${idx}`}
              claim={claim}
              isNew={idx === 0}
            />
          ))
        )}
      </div>
    </div>
  );
}
