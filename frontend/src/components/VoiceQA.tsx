import { useEffect, useRef, useState } from 'react';
import { Loader2, Mic, MicOff } from 'lucide-react';

interface VoiceQAProps {
  sessionId: string;
  ticker: string;
}

const MAX_RECORDING_MS = 10_000;

export function VoiceQA({ sessionId }: VoiceQAProps) {
  const [isRecording, setIsRecording] = useState(false);
  const [isThinking, setIsThinking] = useState(false);
  const [responses, setResponses] = useState<string[]>([]);
  const [errorMsg, setErrorMsg] = useState('');

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
      stopStream();
    };
  }, []);

  function stopStream() {
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== 'inactive') recorder.stop();
    recorder?.stream?.getTracks().forEach((t) => t.stop());
  }

  async function startRecording() {
    setErrorMsg('');
    chunksRef.current = [];

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
    mediaRecorderRef.current = recorder;

    recorder.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data); };
    recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(chunksRef.current, { type: mimeType });
      void submitAudio(blob, mimeType);
    };

    recorder.start();
    setIsRecording(true);

    timeoutRef.current = setTimeout(() => stopRecording(), MAX_RECORDING_MS);
  }

  function stopRecording() {
    if (timeoutRef.current) { clearTimeout(timeoutRef.current); timeoutRef.current = null; }
    setIsRecording(false);
    stopStream();
  }

  async function submitAudio(blob: Blob, mimeType: string) {
    setIsThinking(true);
    const ext = mimeType.includes('webm') ? 'webm' : 'wav';
    const formData = new FormData();
    formData.append('file', blob, `question.${ext}`);

    try {
      const res = await fetch(`/api/session/${sessionId}/sonic-qa`, {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) throw new Error((await res.text()) || `HTTP ${res.status}`);
      const data = await res.json() as { text: string; audio_url: string };
      if (data.text) setResponses((prev) => [data.text, ...prev]);
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Request failed');
    } finally {
      setIsThinking(false);
    }
  }

  function handleMicClick() {
    if (isRecording) stopRecording();
    else if (!isThinking) void startRecording();
  }

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl px-5 py-4 flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-orange-400 uppercase tracking-widest">
          Nova Sonic Voice Q&amp;A
        </span>
        <span className="ml-auto text-xs text-gray-600">Ask questions about the analysis</span>
      </div>

      <div className="flex items-center gap-4">
        <button
          onClick={handleMicClick}
          disabled={isThinking}
          aria-label={isRecording ? 'Stop recording' : 'Start recording'}
          className={[
            'flex-shrink-0 flex items-center justify-center w-11 h-11 rounded-full border-2 transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-gray-900',
            isRecording
              ? 'bg-red-600 border-red-500 text-white focus:ring-red-500 animate-pulse'
              : isThinking
              ? 'bg-gray-700 border-gray-600 text-gray-400 cursor-not-allowed'
              : 'bg-gray-800 border-gray-700 text-gray-300 hover:bg-gray-700 hover:border-gray-500 hover:text-white focus:ring-gray-500',
          ].join(' ')}
        >
          {isThinking ? <Loader2 size={18} className="animate-spin" /> : isRecording ? <MicOff size={18} /> : <Mic size={18} />}
        </button>

        <span className="text-sm text-gray-500">
          {isRecording ? (
            <span className="text-red-400 font-medium flex items-center gap-1.5">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
              Listening… (click to stop)
            </span>
          ) : isThinking ? (
            <span className="text-gray-400">Nova Sonic thinking…</span>
          ) : (
            'Click mic to ask a question'
          )}
        </span>
      </div>

      {errorMsg && (
        <div className="text-xs text-red-400 bg-red-950/30 border border-red-800/50 rounded-lg px-3 py-2">
          {errorMsg}
        </div>
      )}

      {responses.length > 0 && (
        <div className="flex flex-col gap-2 max-h-48 overflow-y-auto">
          {responses.map((text, i) => (
            <p key={i} className={`text-sm leading-relaxed rounded-lg px-3 py-2 ${i === 0 ? 'text-gray-200 bg-gray-800' : 'text-gray-500 bg-gray-900 border border-gray-800'}`}>
              {text}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
