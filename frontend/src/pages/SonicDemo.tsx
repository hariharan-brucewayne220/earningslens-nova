import { useEffect, useRef, useState } from 'react';
import { Mic, MicOff, Volume2, Loader2, ArrowLeft } from 'lucide-react';

const MAX_RECORD_MS = 15_000;

type Status = 'idle' | 'recording' | 'processing' | 'playing' | 'error';

interface Turn {
  id: string;
  type: 'user' | 'nova';
  label: string;
  audioUrl?: string;
  duration?: number;
}

export function SonicDemo({ onBack }: { onBack: () => void }) {
  const [status, setStatus] = useState<Status>('idle');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [errorMsg, setErrorMsg] = useState('');
  const [recordSecs, setRecordSecs] = useState(0);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [turns]);

  useEffect(() => {
    return () => {
      clearTimeout(timeoutRef.current!);
      clearInterval(timerRef.current!);
      stopStream();
    };
  }, []);

  function stopStream() {
    const r = recorderRef.current;
    if (r && r.state !== 'inactive') r.stop();
    r?.stream?.getTracks().forEach((t) => t.stop());
  }

  async function startRecording() {
    setErrorMsg('');
    chunksRef.current = [];
    setRecordSecs(0);

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setErrorMsg('Microphone access denied.');
      return;
    }

    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : 'audio/webm';

    const recorder = new MediaRecorder(stream, { mimeType });
    recorderRef.current = recorder;
    chunksRef.current = [];

    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };

    recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      clearInterval(timerRef.current!);
      const blob = new Blob(chunksRef.current, { type: mimeType });
      void submitAudio(blob);
    };

    recorder.start(250);
    setStatus('recording');

    timerRef.current = setInterval(() => setRecordSecs((s) => s + 1), 1000);
    timeoutRef.current = setTimeout(() => stopRecording(), MAX_RECORD_MS);
  }

  function stopRecording() {
    clearTimeout(timeoutRef.current!);
    clearInterval(timerRef.current!);
    setStatus('processing');
    stopStream();
  }

  async function submitAudio(blob: Blob) {
    setStatus('processing');

    const userTurn: Turn = {
      id: crypto.randomUUID(),
      type: 'user',
      label: `You spoke (${(blob.size / 1024).toFixed(0)} KB)`,
    };
    setTurns((prev) => [...prev, userTurn]);

    const form = new FormData();
    form.append('file', blob, 'question.webm');

    try {
      const res = await fetch('/api/sonic-demo/chat', { method: 'POST', body: form });
      if (!res.ok) {
        const text = await res.text();
        let message = text || `HTTP ${res.status}`;
        try {
          const parsed = JSON.parse(text) as { detail?: string };
          if (parsed.detail) message = parsed.detail;
        } catch {
          // Keep raw text when the response is not JSON.
        }
        throw new Error(message);
      }

      const audioBlob = await res.blob();
      const audioUrl = URL.createObjectURL(audioBlob);

      const novaTurn: Turn = {
        id: crypto.randomUUID(),
        type: 'nova',
        label: 'Nova Sonic responded',
        audioUrl,
      };
      setTurns((prev) => [...prev, novaTurn]);

      // Auto-play
      const audio = new Audio(audioUrl);
      setStatus('playing');
      audio.onended = () => setStatus('idle');
      audio.onerror = () => setStatus('idle');
      await audio.play();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Request failed';
      setErrorMsg(msg);
      setStatus('error');
    }
  }

  function handleMic() {
    if (status === 'recording') stopRecording();
    else if (status === 'idle' || status === 'error') void startRecording();
  }

  const canRecord = status === 'idle' || status === 'error';
  const isRecording = status === 'recording';
  const isProcessing = status === 'processing';
  const isPlaying = status === 'playing';

  return (
    <div
      className="flex flex-col min-h-screen"
      style={{ background: '#060d1f' }}
    >
      {/* Header */}
      <header
        className="flex items-center justify-between px-5 py-3 shrink-0"
        style={{
          background: 'rgba(6,13,31,0.95)',
          borderBottom: '1px solid rgba(255,255,255,0.06)',
          backdropFilter: 'blur(12px)',
        }}
      >
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-300 transition-colors"
          >
            <ArrowLeft size={13} />
            Back
          </button>
          <span style={{ color: 'rgba(255,255,255,0.12)' }}>·</span>
          <div className="flex items-center gap-2">
            <div
              className="w-6 h-6 rounded-md flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #f97316, #ea580c)' }}
            >
              <Volume2 size={12} className="text-white" />
            </div>
            <span className="text-sm font-semibold text-white">Nova Sonic</span>
            <span className="text-xs text-slate-600">Speech-to-Speech Demo</span>
          </div>
        </div>
        <div
          className="flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs"
          style={{
            background: 'rgba(249,115,22,0.08)',
            border: '1px solid rgba(249,115,22,0.2)',
          }}
        >
          <span className="text-slate-500">amazon.nova-2-sonic-v1:0</span>
        </div>
      </header>

      {/* Conversation area */}
      <div className="flex-1 overflow-y-auto px-4 py-6 space-y-4 max-w-2xl mx-auto w-full">
        {turns.length === 0 && (
          <div className="flex flex-col items-center justify-center h-64 gap-4 opacity-50">
            <div
              className="w-16 h-16 rounded-full flex items-center justify-center"
              style={{ background: 'rgba(249,115,22,0.08)', border: '1px solid rgba(249,115,22,0.15)' }}
            >
              <Mic size={24} className="text-orange-400" />
            </div>
            <div className="text-center">
              <p className="text-sm text-slate-400">Press the mic and ask anything</p>
              <p className="text-xs text-slate-600 mt-1">Nova Sonic will respond with voice</p>
            </div>
          </div>
        )}

        {turns.map((turn) => (
          <TurnBubble key={turn.id} turn={turn} />
        ))}

        {isProcessing && (
          <div className="flex items-center gap-3 px-4 py-3 rounded-xl" style={{ background: 'rgba(249,115,22,0.06)', border: '1px solid rgba(249,115,22,0.12)' }}>
            <Loader2 size={14} className="text-orange-400 animate-spin shrink-0" />
            <span className="text-xs text-orange-400">Nova Sonic is thinking…</span>
          </div>
        )}

        {isPlaying && (
          <div className="flex items-center gap-3 px-4 py-3 rounded-xl" style={{ background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.12)' }}>
            <span className="flex gap-0.5">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="w-0.5 rounded-full bg-emerald-400"
                  style={{
                    height: '12px',
                    animation: `soundbar 0.8s ease-in-out infinite`,
                    animationDelay: `${i * 0.15}s`,
                  }}
                />
              ))}
            </span>
            <span className="text-xs text-emerald-400">Nova Sonic speaking…</span>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Error */}
      {errorMsg && (
        <div className="mx-4 mb-2 px-4 py-3 rounded-xl text-xs text-rose-400 max-w-2xl mx-auto w-full" style={{ background: 'rgba(244,63,94,0.08)', border: '1px solid rgba(244,63,94,0.2)' }}>
          {errorMsg}
        </div>
      )}

      {/* Controls */}
      <div
        className="shrink-0 px-4 py-5 flex flex-col items-center gap-3"
        style={{ borderTop: '1px solid rgba(255,255,255,0.06)' }}
      >
        {/* Mic button */}
        <button
          onClick={handleMic}
          disabled={isProcessing || isPlaying}
          aria-label={isRecording ? 'Stop recording' : 'Start recording'}
          className="relative flex items-center justify-center w-16 h-16 rounded-full transition-all duration-200 focus:outline-none"
          style={{
            background: isRecording
              ? 'rgba(244,63,94,0.2)'
              : canRecord
              ? 'rgba(249,115,22,0.12)'
              : 'rgba(255,255,255,0.04)',
            border: isRecording
              ? '2px solid rgba(244,63,94,0.5)'
              : canRecord
              ? '2px solid rgba(249,115,22,0.3)'
              : '2px solid rgba(255,255,255,0.08)',
            boxShadow: isRecording
              ? '0 0 24px rgba(244,63,94,0.3)'
              : canRecord
              ? '0 0 16px rgba(249,115,22,0.15)'
              : 'none',
            cursor: isProcessing || isPlaying ? 'not-allowed' : 'pointer',
          }}
        >
          {isProcessing || isPlaying ? (
            <Loader2 size={22} className="text-slate-500 animate-spin" />
          ) : isRecording ? (
            <MicOff size={22} className="text-rose-400" />
          ) : (
            <Mic size={22} className="text-orange-400" />
          )}

          {/* Pulse ring while recording */}
          {isRecording && (
            <span
              className="absolute inset-0 rounded-full"
              style={{
                border: '2px solid rgba(244,63,94,0.3)',
                animation: 'ping 1s cubic-bezier(0,0,0.2,1) infinite',
              }}
            />
          )}
        </button>

        {/* Status text */}
        <p className="text-xs text-center" style={{ color: 'rgba(148,163,184,0.5)' }}>
          {isRecording
            ? <span className="text-rose-400">{recordSecs}s · Click to send</span>
            : isProcessing
            ? <span className="text-orange-400">Sending to Nova Sonic…</span>
            : isPlaying
            ? <span className="text-emerald-400">Playing response…</span>
            : 'Click to speak'
          }
        </p>
      </div>

      <style>{`
        @keyframes soundbar {
          0%, 100% { transform: scaleY(0.4); }
          50% { transform: scaleY(1); }
        }
        @keyframes ping {
          75%, 100% { transform: scale(1.5); opacity: 0; }
        }
      `}</style>
    </div>
  );
}

function TurnBubble({ turn }: { turn: Turn }) {
  const isUser = turn.type === 'user';

  return (
    <div className={`flex gap-3 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      {/* Avatar */}
      <div
        className="w-7 h-7 rounded-full flex items-center justify-center shrink-0 mt-0.5"
        style={{
          background: isUser
            ? 'rgba(100,116,139,0.15)'
            : 'linear-gradient(135deg, #f97316, #ea580c)',
        }}
      >
        {isUser ? (
          <Mic size={12} style={{ color: 'rgba(148,163,184,0.7)' }} />
        ) : (
          <Volume2 size={12} className="text-white" />
        )}
      </div>

      {/* Bubble */}
      <div
        className="max-w-xs rounded-2xl px-4 py-3 flex flex-col gap-2"
        style={{
          background: isUser
            ? 'rgba(255,255,255,0.04)'
            : 'rgba(249,115,22,0.08)',
          border: `1px solid ${isUser ? 'rgba(255,255,255,0.07)' : 'rgba(249,115,22,0.15)'}`,
        }}
      >
        <p className="text-xs font-medium" style={{ color: isUser ? 'rgba(148,163,184,0.5)' : '#fb923c' }}>
          {isUser ? 'You' : 'Nova Sonic'}
        </p>
        <p className="text-sm text-slate-300">{turn.label}</p>

        {turn.audioUrl && (
          <audio
            controls
            src={turn.audioUrl}
            className="w-full h-7 mt-1"
            style={{ accentColor: '#f97316' }}
          />
        )}
      </div>
    </div>
  );
}
