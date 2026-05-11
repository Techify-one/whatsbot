import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import { sendMessage, retrySend, sendImage, sendAudio, sendPresence, sendPrivateMessage } from '../../services/api.js';
import { SendIcon, BackArrowIcon, DefaultAvatar, GroupAvatar, EmojiIcon, AttachIcon, MicIcon, SingleCheckIcon, DoubleCheckIcon, ClockIcon, FailedIcon, RetryIcon, StopIcon } from './icons.js';
import { formatBubbleTime } from './utils.js';
import { formatWhatsApp } from '../../utils/formatWhatsApp.js';
import { AudioPlayer } from './AudioPlayer.js';

const html = htm.bind(h);

// ── Contact Detail (WhatsApp Web chat panel) ─────────────────────

export function ContactDetail({ phone, onBack, messages, info, contact, onAvatarClick, contactTyping, setContactData, globalTags }) {
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [recording, setRecording] = useState(false);
  const [recordDuration, setRecordDuration] = useState(0);
  // mode: 'reply' sends to the contact; 'private' stays in the panel only
  const [mode, setMode] = useState('reply');
  // Private-mode AI flags. aiReadPrivate=false → AI ignores the note entirely.
  // aiReplyInChat only shown when aiReadPrivate is on; off → AI reply stays as private note.
  const [aiReadPrivate, setAiReadPrivate] = useState(false);
  const [aiReplyInChat, setAiReplyInChat] = useState(true);
  // pendingMedia: { type: 'image'|'audio', file, blob, filename, previewUrl }
  const [pendingMedia, setPendingMedia] = useState(null);
  const chatRef = useRef(null);
  const fileInputRef = useRef(null);
  const inputRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const recordTimerRef = useRef(null);
  const presenceTimerRef = useRef(null);

  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [messages]);

  useEffect(() => {
    setInput('');
    setMode('reply');
    setAiReadPrivate(false);
    setAiReplyInChat(true);
  }, [phone]);

  // Auto-focus message input when opening a chat
  useEffect(() => {
    if (phone && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 150);
    }
  }, [phone]);

  // Send typing presence to contact (debounced)
  function handleInputChange(e) {
    const val = e.target.value;
    setInput(val);
    if (!phone) return;
    // Send "start" on first keystroke, then debounce "stop" after 3s of inactivity
    if (val.trim()) {
      if (!presenceTimerRef.current) {
        sendPresence(phone, 'start').catch(() => {});
      }
      clearTimeout(presenceTimerRef.current);
      presenceTimerRef.current = setTimeout(() => {
        sendPresence(phone, 'stop').catch(() => {});
        presenceTimerRef.current = null;
      }, 3000);
    } else {
      clearTimeout(presenceTimerRef.current);
      presenceTimerRef.current = null;
      sendPresence(phone, 'stop').catch(() => {});
    }
  }

  // Clean up presence timer on unmount or phone change
  useEffect(() => {
    return () => {
      if (presenceTimerRef.current) {
        clearTimeout(presenceTimerRef.current);
        presenceTimerRef.current = null;
        if (phone) sendPresence(phone, 'stop').catch(() => {});
      }
    };
  }, [phone]);

  // Helper to find and update a message by its local ID
  function updateMsgByLocalId(localId, updater) {
    setContactData(prev => {
      if (!prev) return prev;
      const msgs = (prev.messages || []).map(m =>
        m._localId === localId ? { ...m, ...updater(m) } : m
      );
      return { ...prev, messages: msgs };
    });
  }

  function handleKeyDown(e) {
    // Enter sends; Shift+Enter inserts a line break (default behavior).
    // Ignore while IME composition is in progress.
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !e.repeat) {
      e.preventDefault();
      handleSend(e);
    }
  }

  // Auto-resize textarea up to ~6 lines, then scroll
  const INPUT_MAX_HEIGHT = 120;
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, INPUT_MAX_HEIGHT) + 'px';
  }, [input]);

  async function handleSend(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text) return;

    // Stop typing presence
    clearTimeout(presenceTimerRef.current);
    presenceTimerRef.current = null;
    sendPresence(phone, 'stop').catch(() => {});

    setInput('');
    const localId = `local_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const msgTs = Date.now() / 1000;

    if (mode === 'private') {
      setContactData(prev => prev ? {
        ...prev,
        messages: [...(prev.messages || []), {
          role: 'private_note', content: text, ts: msgTs, status: null,
          _localId: localId, _status: 'sending',
        }],
      } : prev);
      try {
        const res = await sendPrivateMessage(phone, text, {
          aiRead: aiReadPrivate,
          aiReply: aiReadPrivate ? aiReplyInChat : true,
        });
        updateMsgByLocalId(localId, () => ({ _status: res.ok ? null : 'failed' }));
      } catch (err) {
        console.error('Private send error:', err);
        updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
      }
      inputRef.current?.focus();
      return;
    }

    // Add message optimistically (status='operator' marks it as manual send)
    setContactData(prev => prev ? {
      ...prev,
      messages: [...(prev.messages || []), {
        role: 'assistant', content: text, ts: msgTs, status: 'operator',
        _localId: localId, _status: 'sending',
      }],
    } : prev);

    try {
      const res = await sendMessage(phone, text);
      if (res.ok) {
        const msgId = res.data?.msg_id || null;
        updateMsgByLocalId(localId, () => ({ _status: null, status: 'operator', msg_id: msgId }));
      } else {
        updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
      }
    } catch (err) {
      console.error('Send error:', err);
      updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
    }
    inputRef.current?.focus();
  }

  async function handleRetry(localId, text) {
    updateMsgByLocalId(localId, () => ({ _status: 'sending', status: 'operator' }));
    try {
      const res = await retrySend(phone, text);
      if (res.ok) {
        updateMsgByLocalId(localId, () => ({ _status: null, status: 'operator' }));
      } else {
        updateMsgByLocalId(localId, () => ({ _status: 'failed', status: 'failed' }));
      }
    } catch (err) {
      console.error('Retry error:', err);
      updateMsgByLocalId(localId, () => ({ _status: 'failed', status: 'failed' }));
    }
  }

  function handleAttachClick() {
    if (fileInputRef.current) fileInputRef.current.click();
  }

  function requestImageSend(file) {
    if (!file || sending || pendingMedia) return;
    const previewUrl = URL.createObjectURL(file);
    setPendingMedia({ type: 'image', file, previewUrl });
  }

  function handleFileSelected(e) {
    const file = e.target.files[0];
    if (file) requestImageSend(file);
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  function handlePaste(e) {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        e.preventDefault();
        const file = item.getAsFile();
        if (file) requestImageSend(file);
        return;
      }
    }
  }

  function cancelPendingMedia() {
    if (pendingMedia?.previewUrl) URL.revokeObjectURL(pendingMedia.previewUrl);
    setPendingMedia(null);
  }

  async function confirmPendingMedia() {
    if (!pendingMedia || sending) return;
    const media = pendingMedia;
    setPendingMedia(null);
    setSending(true);

    const localId = `local_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const localUrl = media.previewUrl || URL.createObjectURL(media.blob || media.file);

    if (media.type === 'image') {
      setContactData(prev => prev ? {
        ...prev,
        messages: [...(prev.messages || []), {
          role: 'assistant', content: '', ts: Date.now() / 1000, status: 'operator',
          media_type: 'image', media_path: localUrl, _localId: localId, _status: 'sending', _isLocalBlob: true,
        }],
      } : prev);
      try {
        const res = await sendImage(phone, media.file);
        updateMsgByLocalId(localId, () => ({ _status: res.ok ? null : 'failed', status: res.ok ? 'operator' : 'failed' }));
      } catch (err) {
        console.error('Send image error:', err);
        updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
      }
    } else {
      // audio
      setContactData(prev => prev ? {
        ...prev,
        messages: [...(prev.messages || []), {
          role: 'assistant', content: '[Áudio]', ts: Date.now() / 1000, status: 'operator',
          media_type: 'audio', media_path: localUrl, _localId: localId, _status: 'sending', _isLocalBlob: true,
        }],
      } : prev);
      try {
        const res = await sendAudio(phone, media.blob, media.filename);
        updateMsgByLocalId(localId, () => ({ _status: res.ok ? null : 'failed', status: res.ok ? 'operator' : 'failed' }));
      } catch (err) {
        console.error('Send audio error:', err);
        updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
      }
    }
    setSending(false);
  }

  async function handleMicClick() {
    if (recording) {
      // Stop recording
      if (mediaRecorderRef.current) {
        mediaRecorderRef.current.stop();
      }
      return;
    }

    // Start recording — uses opus-recorder to produce real OGG/Opus accepted by WhatsApp
    if (typeof window.Recorder !== 'function') {
      alert('Gravador de áudio indisponível: a biblioteca opus-recorder não foi carregada. Recarregue a página (Ctrl+F5) e tente novamente.');
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      alert('Seu navegador não permite acesso ao microfone neste contexto. Abra o WhatsBot via HTTPS (ou http://localhost) para gravar áudios.');
      return;
    }
    try {
      const recorder = new window.Recorder({
        encoderPath: '/static/vendor/opus-recorder/encoderWorker.min.js',
        encoderApplication: 2048, // VOIP
        encoderSampleRate: 48000,
        numberOfChannels: 1,
      });
      mediaRecorderRef.current = recorder;

      recorder.onstart = () => {
        setRecording(true);
        setRecordDuration(0);
        recordTimerRef.current = setInterval(() => setRecordDuration(d => d + 1), 1000);
      };

      recorder.ondataavailable = (blob) => {
        setRecording(false);
        clearInterval(recordTimerRef.current);
        setRecordDuration(0);

        if (!blob || blob.size === 0) return;

        const audioBlob = new Blob([blob], { type: 'audio/ogg' });
        const previewUrl = URL.createObjectURL(audioBlob);
        setPendingMedia({ type: 'audio', blob: audioBlob, filename: 'voice.ogg', previewUrl });
      };

      recorder.onstop = () => {
        setRecording(false);
        clearInterval(recordTimerRef.current);
        setRecordDuration(0);
      };

      await recorder.start();
    } catch (err) {
      console.error('Microphone access error:', err);
      setRecording(false);
      clearInterval(recordTimerRef.current);
      setRecordDuration(0);
      const msg = (err && err.name === 'NotAllowedError')
        ? 'Permissão para o microfone foi negada. Habilite o acesso nas configurações do navegador.'
        : `Não foi possível iniciar a gravação: ${err && err.message ? err.message : err}`;
      alert(msg);
    }
  }

  function formatRecordTime(secs) {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${m}:${s.toString().padStart(2, '0')}`;
  }

  // Empty state — no contact selected
  if (!phone) {
    return html`
      <div class="wa-empty-bg flex flex-col items-center justify-center h-full">
        <div class="mb-8">
          <svg width="250" viewBox="0 0 303 172" class="opacity-20">
            <path fill="#8696a0" d="M229.565 160.229c32.874-12.676 53.009-32.508 53.009-54.669 0-39.356-56.792-71.26-126.87-71.26C85.627 34.3 28.835 66.204 28.835 105.56c0 20.655 17.776 39.174 45.883 51.974a8.372 8.372 0 014.773 5.573l.988 4.89a4.186 4.186 0 006.107 3.312l6.212-3.106a8.372 8.372 0 016.456-.37c12.157 3.96 25.676 6.13 39.95 6.13 7.096 0 14.038-.519 20.772-1.517a8.372 8.372 0 016.164 1.136l7.155 4.479a4.186 4.186 0 006.355-3.438l.247-5.287a8.372 8.372 0 013.636-6.223 8.372 8.372 0 017.258-1.314l17.4 4.64a4.186 4.186 0 005.096-2.013l3.47-6.587a8.372 8.372 0 017.09-4.41z"/>
          </svg>
        </div>
        <h2 class="text-wa-text text-[32px] font-light mb-2">WhatsBot</h2>
        <p class="text-wa-secondary text-[14px] text-center max-w-[450px] leading-[20px]">
          Envie e receba mensagens. Selecione um contato para começar.
        </p>
        <div class="mt-10 flex items-center gap-2 text-wa-secondary text-[12px]">
          <svg viewBox="0 0 10 12" width="10" height="12"><path fill="#8696a0" d="M5.063 0C2.272 0 .006 2.274.006 5.078v1.715L0 6.792v.7l.006.007v.206C.006 9.708 2.272 12 5.063 12h.037C7.89 12 10.1 9.708 10.1 6.905v-.2l.007-.008v-.7l-.007-.001V5.078C10.1 2.274 7.89 0 5.1 0h-.037zm0 1.2h.037c2.146 0 3.837 1.71 3.837 3.878v1.138l-.87.862v.827c0 2.168-1.69 3.895-3.837 3.895h-.037c-2.147 0-3.857-1.727-3.857-3.895v-.827l-.87-.862V5.078c0-2.168 1.71-3.878 3.857-3.878z"/></svg>
          Criptografia de ponta a ponta
        </div>
      </div>
    `;
  }

  const isGroup = contact && contact.is_group;
  const canSend = contact ? (contact.can_send !== false) : true;
  const rawName = info && info.name;
  const isAutoName = !isGroup && rawName && rawName.startsWith('~');
  const displayName = isGroup ? (contact.group_name || phone) : (rawName ? rawName.replace(/^~/, '') : phone);
  const hasText = input.trim().length > 0;

  return html`
    <div class="flex flex-col h-full">
      <!-- Header -->
      <div class="h-[59px] flex items-center px-4 bg-wa-panel border-b border-wa-border shrink-0">
        <button onClick=${onBack} class="lg:hidden text-wa-icon hover:text-wa-text mr-2 shrink-0">
          <${BackArrowIcon} />
        </button>
        <div onClick=${onAvatarClick} class="w-[40px] h-[40px] rounded-full overflow-hidden shrink-0 mr-[13px] cursor-pointer">
          ${isGroup
            ? html`<${GroupAvatar} size=${40} avatarUrl=${phone ? "/statics/avatars/" + phone + ".jpg" : null} />`
            : html`<${DefaultAvatar} size=${40} avatarUrl=${phone ? "/statics/avatars/" + phone + ".jpg" : null} />`
          }
        </div>
        <div class="flex-1 min-w-0 cursor-pointer" onClick=${onAvatarClick}>
          <div class="text-wa-text text-[16px] leading-tight truncate flex items-center gap-[6px]">
            <span class="truncate">${displayName}</span>${isAutoName ? html`<span class="text-[10px] font-semibold text-blue-400 bg-blue-500/15 rounded px-[5px] py-[1px] shrink-0" title="Nome obtido do WhatsApp">WA</span>` : null}${contact && contact.tags && contact.tags.length > 0 ? contact.tags.map(tagName => {
              const tagInfo = globalTags && globalTags[tagName];
              const color = tagInfo ? tagInfo.color : '#6b7280';
              return html`<span
                class="text-[9px] font-semibold rounded-full px-[5px] py-[0.5px] leading-[14px] shrink-0"
                style="background: ${color}20; color: ${color}; border: 1px solid ${color}40;"
              >${tagName}</span>`;
            }) : null}
          </div>
          ${contactTyping
            ? html`<div class="text-wa-teal text-[13px] leading-tight">${contactTyping === 'audio' ? 'gravando áudio...' : 'digitando...'}</div>`
            : isGroup ? html`<div class="text-wa-secondary text-[13px] leading-tight">Grupo</div>`
            : info && info.name ? html`<div class="text-wa-secondary text-[13px] leading-tight">${phone}</div>` : null
          }
        </div>
      </div>

      <!-- Chat area with doodle pattern -->
      <div ref=${chatRef} class="flex-1 min-h-0 overflow-y-auto wa-scrollbar wa-chat-pattern py-2 px-[4%] lg:px-[7%]">
        ${!messages || messages.length === 0
          ? html`<div class="text-center text-wa-secondary py-8 text-[14px]">
              <span class="bg-white/80 rounded-lg px-3 py-1.5 text-[12.5px] shadow-sm">Nenhuma mensagem ainda</span>
            </div>`
          : messages.map((m, i) => {
              const isUser = m.role === 'user';
              const isTranscription = m.role === 'transcription';
              const isPrivateNote = m.role === 'private_note';
              const isSystemNotice = m.role === 'system_notice';
              const isToolCall = m.role === 'tool_call';
              const isError = m.role === 'error';
              const isFirst = i === 0 || messages[i - 1].role !== m.role;

              if (isPrivateNote) {
                const failed = m._status === 'failed';
                const pending = m._status === 'sending';
                return html`
                  <div key=${m._localId || i} class="flex justify-center mt-[4px]">
                    <div class="max-w-[75%] rounded-[7.5px] px-[11px] pt-[6px] pb-[7px] text-[13px] leading-[18px] whitespace-pre-wrap relative shadow-sm"
                         style="background:#3b266b; color:#ede9fe; border:1px solid #7c3aed; ${failed ? 'opacity:0.7;' : ''}">
                      <span class="flex items-center gap-[5px] text-[10.5px] font-semibold mb-[3px] tracking-wide uppercase" style="color:#c4b5fd;">
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1s3.1 1.39 3.1 3.1v2z"/></svg>
                        Mensagem privada
                      </span>
                      <span dangerouslySetInnerHTML=${{ __html: formatWhatsApp(m.content) }}></span>
                      <span class="float-right ml-[8px] mt-[3px] text-[10.5px] leading-[14px] whitespace-nowrap" style="color:#a78bfa;">
                        ${pending ? '⏳ ' : (failed ? '⚠ ' : '')}${formatBubbleTime(m.ts)}
                      </span>
                    </div>
                  </div>
                `;
              }

              if (isTranscription) {
                return html`
                  <div key=${i} class="flex justify-center mt-[4px]">
                    <div class="max-w-[75%] rounded-[7.5px] px-[10px] pt-[5px] pb-[6px] text-[12.5px] leading-[17px] whitespace-pre-wrap relative"
                         style="background: #2d1b4e; color: #d4bfff; border: 1px solid #4a2d7a;">
                      <span class="flex items-center gap-1 text-[10px] font-semibold mb-[2px] opacity-80">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1s3.1 1.39 3.1 3.1v2z"/></svg>
                        Transcrição privada
                      </span>
                      <span dangerouslySetInnerHTML=${{ __html: formatWhatsApp(m.content) }}></span>
                      <span class="float-right ml-[8px] mt-[2px] text-[10px] leading-[14px] whitespace-nowrap opacity-60">
                        ${formatBubbleTime(m.ts)}
                      </span>
                    </div>
                  </div>
                `;
              }

              if (isSystemNotice) {
                return html`
                  <div key=${i} class="flex justify-center mt-[4px]">
                    <div class="max-w-[75%] rounded-[7.5px] px-[10px] pt-[5px] pb-[6px] text-[12.5px] leading-[17px] whitespace-pre-wrap relative"
                         style="background: #1b2e4e; color: #93c5fd; border: 1px solid #1e40af;">
                      <span class="flex items-center gap-1 text-[10px] font-semibold mb-[2px] opacity-80">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
                        Mensagem do Sistema
                      </span>
                      <span dangerouslySetInnerHTML=${{ __html: formatWhatsApp(m.content) }}></span>
                      <span class="float-right ml-[8px] mt-[2px] text-[10px] leading-[14px] whitespace-nowrap opacity-60">
                        ${formatBubbleTime(m.ts)}
                      </span>
                    </div>
                  </div>
                `;
              }

              if (isToolCall) {
                return html`
                  <div key=${i} class="flex justify-center mt-[4px]">
                    <div class="max-w-[75%] rounded-[7.5px] px-[10px] pt-[5px] pb-[6px] text-[12.5px] leading-[17px] whitespace-pre-wrap relative"
                         style="background: #2d1b0e; color: #fbbf24; border: 1px solid #78350f;">
                      <span class="flex items-center gap-1 text-[10px] font-semibold mb-[2px] opacity-80">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.7C.4 7.1.9 10.1 2.9 12.1c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1 .4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.4z"/></svg>
                        Ferramenta IA
                      </span>
                      <span dangerouslySetInnerHTML=${{ __html: formatWhatsApp(m.content) }}></span>
                      <span class="float-right ml-[8px] mt-[2px] text-[10px] leading-[14px] whitespace-nowrap opacity-60">
                        ${formatBubbleTime(m.ts)}
                      </span>
                    </div>
                  </div>
                `;
              }

              if (isError) {
                return html`
                  <div key=${i} class="flex justify-center mt-[4px]">
                    <div class="max-w-[85%] rounded-[7.5px] px-[10px] pt-[5px] pb-[6px] text-[12.5px] leading-[17px] whitespace-pre-wrap relative"
                         style="background: #fef2f2; color: #dc2626; border: 1px solid #fecaca;">
                      <span class="flex items-center gap-1 text-[10px] font-semibold mb-[2px] opacity-80">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
                        Erro no envio
                      </span>
                      <span dangerouslySetInnerHTML=${{ __html: formatWhatsApp(m.content) }}></span>
                      <span class="float-right ml-[8px] mt-[2px] text-[10px] leading-[14px] whitespace-nowrap opacity-60">
                        ${formatBubbleTime(m.ts)}
                      </span>
                    </div>
                  </div>
                `;
              }

              const isFailed = m._status === 'failed' || m.status === 'failed';
              const isSending = m._status === 'sending';
              const isOperator = !isUser && m.status === 'operator';

              // In groups, the backend prefixes user content with "[Sender Name]: text"
              // for LLM context. Strip the prefix here and use the sender name as label.
              let displayContent = m.content;
              let groupSender = null;
              if (isUser && isGroup && typeof m.content === 'string') {
                const match = m.content.match(/^\[([^\]]+)\]:\s*([\s\S]*)$/);
                if (match) {
                  groupSender = match[1];
                  displayContent = match[2];
                }
              }

              const senderLabel = isUser ? (groupSender || displayName) : (isOperator ? 'Manual' : 'IA');
              const senderColor = isUser ? '#1f7aec' : (isOperator ? '#b45309' : '#047857');

              return html`
                <div key=${m._localId || i} class="flex ${isUser ? 'justify-start' : 'justify-end'} ${isFirst ? 'mt-[12px]' : 'mt-[2px]'}">
                  <div class="wa-bubble max-w-[65%] rounded-[7.5px] px-[9px] pt-[6px] pb-[8px] text-[14.2px] leading-[19px] whitespace-pre-wrap relative ${
                    isUser
                      ? `bg-wa-incoming text-wa-text ${isFirst ? 'msg-tail-in rounded-tl-none' : ''}`
                      : `${isFailed ? 'text-wa-text' : 'bg-wa-outgoing text-wa-text'} ${isFirst ? 'msg-tail-out rounded-tr-none' : ''}`
                  }" style="${isFailed ? 'background: #fce8e8;' : ''}">
                    <span class="block text-[11px] font-semibold leading-[13px] mb-[2px] truncate" style="color: ${senderColor};">${senderLabel}</span>
                    ${m.media_type === 'image' ? html`
                      <img
                        src="${m._isLocalBlob ? m.media_path : '/' + m.media_path}"
                        alt="Imagem"
                        class="rounded-[4px] max-w-full max-h-[300px] mb-1 cursor-pointer"
                        style="min-width:120px"
                        onClick=${() => window.open(m._isLocalBlob ? m.media_path : '/' + m.media_path, '_blank')}
                        loading="lazy"
                      />
                      ${displayContent && displayContent !== '[Imagem enviada pelo contato]' && !displayContent.startsWith('[Descrição da imagem]')
                        ? html`<span dangerouslySetInnerHTML=${{ __html: formatWhatsApp(displayContent) }}></span>`
                        : null}
                    ` : m.media_type === 'audio' ? html`
                      <${AudioPlayer} src=${m.media_path} isLocalBlob=${m._isLocalBlob} />
                      ${displayContent && displayContent !== '[Áudio recebido]' && displayContent !== '[Áudio]' && !displayContent.startsWith('[Transcrição do áudio]')
                        ? html`<span class="block text-[12px] text-wa-secondary italic" dangerouslySetInnerHTML=${{ __html: formatWhatsApp(displayContent) }}></span>`
                        : null}
                    ` : html`<span dangerouslySetInnerHTML=${{ __html: formatWhatsApp(displayContent) }}></span>`}
                    <span class="float-right ml-[8px] mt-[4px] text-[11px] leading-[15px] whitespace-nowrap text-wa-secondary">
                      ${!isUser ? (() => {
                        if (isFailed) return html`<${FailedIcon} />${!m.media_type && m._localId ? html`<${RetryIcon} onClick=${() => handleRetry(m._localId, m.content)} />` : ''}`;
                        if (isSending) return html`<${ClockIcon} />`;
                        const st = m.status || m._status;
                        if (st === 'sent') return html`<${SingleCheckIcon} />`;
                        if (st === 'delivered') return html`<${DoubleCheckIcon} color="#92a58c" />`;
                        if (st === 'read') return html`<${DoubleCheckIcon} />`;
                        if (st === 'operator') return html`<${DoubleCheckIcon} color="#92a58c" />`;
                        return html`<${DoubleCheckIcon} />`;
                      })() : ''}${formatBubbleTime(m.ts)}
                    </span>
                  </div>
                </div>
              `;
            })
        }
      </div>

      <!-- Hidden file input for image upload -->
      <input
        ref=${fileInputRef}
        type="file"
        accept="image/*"
        class="hidden"
        onChange=${handleFileSelected}
      />

      <!-- Media confirmation overlay -->
      ${pendingMedia && canSend ? html`
        <div class="flex flex-col items-center bg-wa-panel border-t border-wa-border px-[16px] py-[12px] shrink-0 gap-[10px]">
          ${pendingMedia.type === 'image' ? html`
            <img src=${pendingMedia.previewUrl} class="max-h-[200px] max-w-full rounded-[8px] object-contain" />
          ` : html`
            <div class="w-full max-w-[320px]">
              <${AudioPlayer} src=${pendingMedia.previewUrl} isLocalBlob=${true} />
            </div>
          `}
          <div class="flex gap-[12px]">
            <button
              type="button"
              onClick=${cancelPendingMedia}
              class="px-[16px] py-[6px] rounded-[8px] text-[13px] bg-wa-hover text-wa-text border border-wa-border hover:bg-wa-inputBg transition-colors"
            >Cancelar</button>
            <button
              type="button"
              onClick=${confirmPendingMedia}
              disabled=${sending}
              class="px-[16px] py-[6px] rounded-[8px] text-[13px] bg-wa-outgoing text-wa-text border border-wa-border hover:opacity-90 transition-colors disabled:opacity-50 flex items-center gap-[6px]"
            ><${SendIcon} /> Enviar</button>
          </div>
        </div>
      ` : ''}

      <!-- Input area -->
      ${!canSend ? html`
        <div class="flex items-center justify-center px-[10px] py-[14px] bg-wa-panel min-h-[62px] shrink-0 border-t border-wa-border">
          <span class="text-wa-secondary text-[14px] flex items-center gap-[6px]">
            <svg class="w-[16px] h-[16px]" viewBox="0 0 24 24" fill="currentColor">
              <path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1s3.1 1.39 3.1 3.1v2z"/>
            </svg>
            Você não pode enviar mensagens neste grupo
          </span>
        </div>
      ` : pendingMedia ? '' : recording ? html`
        <div class="flex items-center px-[10px] py-[5px] bg-wa-panel min-h-[62px] shrink-0">
          <div class="flex-1 flex items-center gap-3 mx-[5px]">
            <span class="w-[10px] h-[10px] rounded-full bg-red-500 animate-pulse shrink-0"></span>
            <span class="text-red-500 text-[15px] font-medium">${formatRecordTime(recordDuration)}</span>
            <span class="text-wa-secondary text-[14px]">Gravando...</span>
          </div>
          <button
            type="button"
            onClick=${handleMicClick}
            class="p-[8px] shrink-0"
          >
            <${StopIcon} />
          </button>
        </div>
      ` : html`
        <div class="flex items-center gap-[10px] flex-wrap px-[14px] pt-[7px] pb-[3px] bg-wa-panel shrink-0">
          <div class="inline-flex items-center gap-[2px] p-[3px] rounded-full" style="background:#111b21;">
            <button
              type="button"
              onClick=${() => setMode('reply')}
              class="text-[12px] font-medium px-[14px] py-[4px] rounded-full transition-colors"
              style="background:${mode === 'reply' ? '#005c4b' : 'transparent'}; color:${mode === 'reply' ? '#ffffff' : '#aebac1'};"
            >Responder</button>
            <button
              type="button"
              onClick=${() => setMode('private')}
              class="text-[12px] font-medium px-[14px] py-[4px] rounded-full transition-colors flex items-center gap-[5px]"
              style="background:${mode === 'private' ? '#7c3aed' : 'transparent'}; color:${mode === 'private' ? '#ffffff' : '#aebac1'};"
            >
              <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1s3.1 1.39 3.1 3.1v2z"/></svg>
              Mensagem Privada
            </button>
          </div>
          ${mode === 'private' ? html`
            <label class="inline-flex items-center gap-[6px] cursor-pointer select-none" title="Quando ligado, a IA processa a mensagem privada como instrução.">
              <input
                type="checkbox"
                class="sr-only peer"
                checked=${aiReadPrivate}
                onChange=${e => setAiReadPrivate(e.target.checked)}
              />
              <div class="relative w-[28px] h-[16px] bg-gray-500 rounded-full peer-checked:bg-violet-500 transition-colors after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-[12px] after:w-[12px] after:transition-transform peer-checked:after:translate-x-[12px]"></div>
              <span class="text-[12px] text-wa-secondary">IA lê</span>
            </label>
            ${aiReadPrivate ? html`
              <label class="inline-flex items-center gap-[6px] cursor-pointer select-none" title="Quando ligado, a IA responde no chat do contato. Quando desligado, a resposta fica apenas como nota privada.">
                <input
                  type="checkbox"
                  class="sr-only peer"
                  checked=${aiReplyInChat}
                  onChange=${e => setAiReplyInChat(e.target.checked)}
                />
                <div class="relative w-[28px] h-[16px] bg-gray-500 rounded-full peer-checked:bg-violet-500 transition-colors after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-[12px] after:w-[12px] after:transition-transform peer-checked:after:translate-x-[12px]"></div>
                <span class="text-[12px] text-wa-secondary">IA responde no chat</span>
              </label>
            ` : ''}
          ` : ''}
        </div>
        <form onSubmit=${handleSend} class="flex items-center px-[10px] py-[5px] bg-wa-panel min-h-[62px] shrink-0">
          <button type="button" class="p-[8px] shrink-0" tabindex="-1">
            <${EmojiIcon} />
          </button>
          ${mode === 'private' ? '' : html`
            <button type="button" class="p-[8px] shrink-0" tabindex="-1" onClick=${handleAttachClick}>
              <${AttachIcon} />
            </button>
          `}
          <div class="flex-1 mx-[5px]">
            <textarea
              ref=${inputRef}
              rows="1"
              value=${input}
              onInput=${handleInputChange}
              onKeyDown=${handleKeyDown}
              onPaste=${handlePaste}
              placeholder=${mode === 'private' ? 'Mensagem privada' : 'Digite uma mensagem'}
              class="w-full block bg-wa-inputBg text-wa-text text-[15px] rounded-[8px] px-[12px] py-[9px] border border-wa-border outline-none placeholder-wa-secondary resize-none max-h-[120px] wa-scrollbar leading-[20px]"
            ></textarea>
          </div>
          ${hasText ? html`
            <button
              type="submit"
              class="p-[8px] shrink-0 transition-colors"
              style="color: ${mode === 'private' ? '#a78bfa' : '#00a884'};"
            >
              <${SendIcon} />
            </button>
          ` : mode === 'private' ? '' : html`
            <button type="button" class="p-[8px] shrink-0 text-wa-icon" tabindex="-1" onClick=${handleMicClick}>
              <${MicIcon} />
            </button>
          `}
        </form>
      `}
    </div>
  `;
}
