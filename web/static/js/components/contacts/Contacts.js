import { h } from 'preact';
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import htm from 'htm';
import { getContacts, getContact, markAsRead, toggleContactAI, getTags, deleteContact, archiveContact, checkPhone } from '../../services/api.js';
import { ContactList } from './ContactList.js';
import { ContactDetail } from './ContactDetail.js';
import { ContactInfoPanel } from './ContactInfoPanel.js';
import { ContextMenu } from './ContextMenu.js';

const html = htm.bind(h);

// ── Main Component ───────────────────────────────────────────────

export function Contacts({ newMessage, chatPresence, contactInfoUpdated, tagsChanged, contactTagsUpdated, contactAiToggled, messagesRead, messageStatus, initialContactId, wsConnected, config, onConfigSave }) {
  const [contacts, setContacts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(null);
  const [contactData, setContactData] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const hasLoadedDetail = useRef(false);
  const [showInfoPanel, setShowInfoPanel] = useState(false);
  const openInfoAfterSelect = useRef(false);
  const [sidebarHidden, setSidebarHidden] = useState(false);
  const [ctxMenu, setCtxMenu] = useState(null);
  const [typingState, setTypingState] = useState({});  // { phone: 'text'|'audio'|null }
  const [showArchived, setShowArchived] = useState(false);
  const [globalTags, setGlobalTags] = useState({});
  const [checkingPhone, setCheckingPhone] = useState(false);
  const [checkPhoneError, setCheckPhoneError] = useState(null);
  const pendingWsMessages = useRef({});
  const selectedRef = useRef(null);
  const typingTimers = useRef({});
  const contactsRef = useRef([]);
  const lastResolvedId = useRef(null);
  const pageVisibleRef = useRef(!document.hidden);

  // Keep refs in sync — avoids stale closures
  useEffect(() => { selectedRef.current = selected; }, [selected]);
  useEffect(() => { contactsRef.current = contacts; }, [contacts]);

  // Track page visibility — mark selected contact as read when tab becomes visible
  useEffect(() => {
    const handler = () => {
      const visible = !document.hidden;
      pageVisibleRef.current = visible;
      if (visible && selectedRef.current) {
        markAsRead(selectedRef.current);
        setContacts(prev => prev.map(c =>
          c.phone === selectedRef.current ? { ...c, unread_count: 0, unread_ai_count: 0 } : c
        ));
      }
    };
    document.addEventListener('visibilitychange', handler);
    return () => document.removeEventListener('visibilitychange', handler);
  }, []);

  const handleToggleAI = useCallback(async (phone, enabled) => {
    const res = await toggleContactAI(phone, enabled);
    if (res.ok) {
      setContacts(prev => prev.map(c =>
        c.phone === phone ? { ...c, ai_enabled: res.data.ai_enabled } : c
      ));
      if (contactData && contactData.phone === phone) {
        setContactData(prev => prev ? { ...prev, ai_enabled: res.data.ai_enabled } : prev);
      }
    }
  }, [contactData]);

  const handleArchive = useCallback(async (phone, archived) => {
    const res = await archiveContact(phone, archived);
    if (res.ok) {
      setContacts(prev => prev.filter(c => c.phone !== phone));
      if (selectedRef.current === phone) {
        setSelected(null);
        setContactData(null);
        history.pushState(null, '', '/');
      }
    }
  }, []);

  const handleDelete = useCallback(async (phone) => {
    const res = await deleteContact(phone);
    if (res.ok) {
      setContacts(prev => prev.filter(c => c.phone !== phone));
      if (selectedRef.current === phone) {
        setSelected(null);
        setContactData(null);
        history.pushState(null, '', '/');
      }
    }
  }, []);

  // Push URL when selecting/deselecting a contact
  const selectContact = useCallback((phone) => {
    setSelected(phone);
    if (phone) {
      const c = contactsRef.current.find(c => c.phone === phone);
      if (c && c.id != null) {
        history.pushState(null, '', `/contacts/${c.id}`);
      }
    } else {
      history.pushState(null, '', '/');
    }
  }, []);

  const handleSearchChange = useCallback((val) => {
    setSearch(val);
    setCheckPhoneError(null);
  }, []);

  const showArchivedRef = useRef(false);
  useEffect(() => { showArchivedRef.current = showArchived; }, [showArchived]);

  const fetchContacts = useCallback((q = '') => {
    setLoading(true);
    getContacts(q, showArchivedRef.current).then(res => {
      if (res.ok) {
        setContacts(res.data);
        contactsRef.current = res.data;
      }
      setLoading(false);
    });
  }, []);

  const handleStartConversation = useCallback(async (normalizedPhone) => {
    if (!normalizedPhone || checkingPhone) return;

    setCheckingPhone(true);
    setCheckPhoneError(null);

    try {
      const res = await checkPhone(normalizedPhone);
      if (!res.ok) {
        setCheckPhoneError(res.error || 'Erro ao verificar número.');
        setCheckingPhone(false);
        return;
      }

      if (!res.data.registered) {
        setCheckPhoneError('Este número não possui WhatsApp.');
        setCheckingPhone(false);
        return;
      }

      // Number is valid — use canonical phone from API (avoids BR duplicates)
      const canonicalPhone = res.data.phone || normalizedPhone;
      setCheckingPhone(false);
      setCheckPhoneError(null);
      setSearch('');
      selectContact(canonicalPhone);
      fetchContacts();
    } catch (e) {
      setCheckPhoneError('Erro ao verificar número. Tente novamente.');
      setCheckingPhone(false);
    }
  }, [checkingPhone, selectContact, fetchContacts]);

  const handleToggleArchived = useCallback(() => {
    setShowArchived(prev => !prev);
    setSelected(null);
  }, []);

  // Initial load
  useEffect(() => { fetchContacts(); }, []);

  // Load global tags
  useEffect(() => {
    getTags().then(res => { if (res.ok) setGlobalTags(res.data); });
  }, []);

  // Reload when archive filter changes
  useEffect(() => { fetchContacts(search); }, [showArchived]);

  // Resolve initialContactId → phone when contacts are loaded
  useEffect(() => {
    if (initialContactId == null) {
      // popstate back to "/" — deselect without pushing URL again
      if (lastResolvedId.current != null) {
        setSelected(null);
        lastResolvedId.current = null;
      }
      return;
    }
    // Already resolved this exact ID — skip (prevents re-selecting on contacts list refresh)
    if (initialContactId === lastResolvedId.current) return;
    if (contacts.length === 0 || loading) return;
    const c = contacts.find(c => c.id === initialContactId);
    if (c) {
      setSelected(c.phone);
      lastResolvedId.current = initialContactId;
    }
  }, [initialContactId, contacts, loading]);

  // Debounced search
  useEffect(() => {
    const timer = setTimeout(() => fetchContacts(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  // Load contact detail when selected changes
  useEffect(() => {
    if (!selected) { setContactData(null); return; }
    if (openInfoAfterSelect.current) {
      openInfoAfterSelect.current = false;
      setShowInfoPanel(true);
    } else {
      setShowInfoPanel(false);
    }
    if (!hasLoadedDetail.current) setLoadingDetail(true);
    // Preserve any messages already buffered for this contact (arrived before selection)
    // but reset the accumulator for new messages arriving during fetch
    const preFetchBuffer = pendingWsMessages.current[selected] || [];
    pendingWsMessages.current[selected] = [];
    // Clear unread badges immediately in local state (only if page is visible)
    const isPageVisible = pageVisibleRef.current;
    if (isPageVisible) {
      setContacts(prev => prev.map(c =>
        c.phone === selected ? { ...c, unread_count: 0, unread_ai_count: 0 } : c
      ));
    }
    getContact(selected, isPageVisible).then(res => {
      if (res.ok) {
        const data = res.data;
        // Merge buffered messages: pre-fetch (arrived before click) + during-fetch (arrived during loading)
        const duringFetch = pendingWsMessages.current[selected] || [];
        const pending = [...preFetchBuffer, ...duringFetch];
        if (pending.length > 0) {
          const existing = data.messages || [];
          const newMsgs = pending.filter(m =>
            !existing.some(e =>
              (e.ts === m.ts && e.role === m.role) ||
              (e.role === m.role && e.content === m.content && Math.abs(e.ts - m.ts) < 30)
            )
          );
          if (newMsgs.length > 0) {
            data.messages = [...(data.messages || []), ...newMsgs];
          }
        }
        // Hydrate failed messages with _localId so retry button works after reload
        data.messages = (data.messages || []).map(m => {
          if (m.status === 'failed') {
            return { ...m, _localId: `loaded_${m.ts}`, _status: 'failed' };
          }
          return m;
        });
        pendingWsMessages.current[selected] = [];
        setContactData(data);
      }
      hasLoadedDetail.current = true;
      setLoadingDetail(false);
    });
  }, [selected]);

  // Handle chat presence events (typing/recording indicators)
  useEffect(() => {
    if (!chatPresence) return;
    const { phone, state, media } = chatPresence;
    if (!phone) return;

    if (state === 'composing') {
      setTypingState(prev => ({ ...prev, [phone]: media === 'audio' ? 'audio' : 'text' }));
      // Auto-clear after 5s if no "paused" arrives
      clearTimeout(typingTimers.current[phone]);
      typingTimers.current[phone] = setTimeout(() => {
        setTypingState(prev => { const n = { ...prev }; delete n[phone]; return n; });
      }, 5000);
    } else {
      // paused or unknown → clear
      clearTimeout(typingTimers.current[phone]);
      setTypingState(prev => { const n = { ...prev }; delete n[phone]; return n; });
    }
  }, [chatPresence]);

  // Handle real-time contact info updates (e.g. from save_contact_info tool)
  useEffect(() => {
    if (!contactInfoUpdated) return;
    const { phone, info: updatedInfo } = contactInfoUpdated;
    console.log('[WS] contact_info_updated', phone, updatedInfo);
    if (!phone || !updatedInfo) return;

    // Update sidebar name
    setContacts(prev => prev.map(c =>
      c.phone === phone ? { ...c, name: updatedInfo.name || c.name } : c
    ));

    // Update detail view if this contact is selected
    if (phone === selectedRef.current) {
      setContactData(prev => prev ? { ...prev, info: { ...updatedInfo } } : prev);
    }
  }, [contactInfoUpdated]);

  // Handle global tags registry changes (create/update/delete)
  useEffect(() => {
    if (!tagsChanged) return;
    setGlobalTags(tagsChanged);
  }, [tagsChanged]);

  // Handle real-time AI toggle (e.g. from transfer_to_human tool)
  useEffect(() => {
    if (!contactAiToggled) return;
    const { phone, ai_enabled } = contactAiToggled;
    if (!phone) return;
    setContacts(prev => prev.map(c =>
      c.phone === phone ? { ...c, ai_enabled } : c
    ));
    if (phone === selectedRef.current) {
      setContactData(prev => prev ? { ...prev, ai_enabled } : prev);
    }
  }, [contactAiToggled]);

  // Handle contact-level tag changes
  useEffect(() => {
    if (!contactTagsUpdated) return;
    const { phone, tags } = contactTagsUpdated;
    if (!phone) return;
    setContacts(prev => prev.map(c =>
      c.phone === phone ? { ...c, tags } : c
    ));
    if (phone === selectedRef.current) {
      setContactData(prev => prev ? { ...prev, tags } : prev);
    }
  }, [contactTagsUpdated]);

  // Handle messages read (WhatsApp mobile ack or AI auto-read)
  useEffect(() => {
    if (!messagesRead) return;
    const { phone, only_user } = messagesRead;
    if (!phone) return;
    setContacts(prev => prev.map(c =>
      c.phone === phone
        ? { ...c, unread_count: 0, ...(only_user ? {} : { unread_ai_count: 0 }) }
        : c
    ));
  }, [messagesRead]);

  // Handle delivery/read status updates for outgoing messages
  useEffect(() => {
    if (!messageStatus) return;
    const { msg_ids, status } = messageStatus;
    if (!msg_ids || !status) return;
    // Always try to update messages by msg_id in the current detail view
    setContactData(prev => {
      if (!prev || !prev.messages) return prev;
      let changed = false;
      const updated = prev.messages.map(m => {
        if (m.msg_id && msg_ids.includes(m.msg_id) && m.status !== status) {
          changed = true;
          return { ...m, status };
        }
        return m;
      });
      return changed ? { ...prev, messages: updated } : prev;
    });
    // Update sidebar last message status (forward-only: sent → delivered → read)
    const { phone } = messageStatus;
    if (phone) {
      const STATUS_ORDER = { sent: 1, delivered: 2, read: 3 };
      setContacts(prev => prev.map(c => {
        if (c.phone === phone && c.last_message_role === 'assistant'
            && (STATUS_ORDER[status] || 0) > (STATUS_ORDER[c.last_message_status] || 0)) {
          return { ...c, last_message_status: status };
        }
        return c;
      }));
    }
  }, [messageStatus]);

  // Sync last assistant message status from chat detail → sidebar
  // Covers both WS updates and fresh data from API fetch
  useEffect(() => {
    if (!contactData || !contactData.messages || !selected) return;
    const msgs = contactData.messages;
    // Find the last visible (non-transcription/system) assistant message
    for (let i = msgs.length - 1; i >= 0; i--) {
      const m = msgs[i];
      if (m.role === 'assistant' && m.status) {
        setContacts(prev => prev.map(c => {
          if (c.phone === selected && c.last_message_role === 'assistant' && m.status !== c.last_message_status) {
            return { ...c, last_message_status: m.status };
          }
          return c;
        }));
        break;
      }
    }
  }, [contactData, selected]);

  // Handle real-time messages from WebSocket
  useEffect(() => {
    if (!newMessage) return;
    const { phone, message } = newMessage;

    // Update detail view if this contact is selected
    // Use selectedRef to avoid stale closure
    if (phone === selectedRef.current) {
      // Use functional updater — prev is always the latest contactData
      setContactData(prev => {
        if (!prev) {
          // Contact data still loading — buffer in per-phone map
          const buf = pendingWsMessages.current[phone] || [];
          if (!buf.some(m =>
            (m.ts === message.ts && m.role === message.role) ||
            (m.role === message.role && m.content === message.content && Math.abs(m.ts - message.ts) < 30)
          )) {
            pendingWsMessages.current[phone] = [...buf, message];
          }
          return prev;
        }
        // Deduplicate by ts + role, or by content + role (within 30s window)
        const dupIdx = prev.messages ? prev.messages.findIndex(m =>
          (m.ts === message.ts && m.role === message.role) ||
          (m.role === message.role && m.content === message.content && Math.abs(m.ts - message.ts) < 30)
        ) : -1;
        if (dupIdx !== -1) {
          // Merge msg_id and status from server into existing message
          if (message.msg_id || message.status) {
            const updated = [...prev.messages];
            updated[dupIdx] = { ...updated[dupIdx],
              ...(message.msg_id ? { msg_id: message.msg_id } : {}),
              ...(message.status && !updated[dupIdx]._status ? { status: message.status } : {}),
            };
            return { ...prev, messages: updated };
          }
          return prev;
        }
        return {
          ...prev,
          messages: [...(prev.messages || []), message],
          updated_at: message.ts,
        };
      });
      if (message.role === 'user' && pageVisibleRef.current) markAsRead(phone);
    } else {
      // Contact NOT selected — buffer for when it's opened
      const buf = pendingWsMessages.current[phone] || [];
      if (!buf.some(m =>
        (m.ts === message.ts && m.role === message.role) ||
        (m.role === message.role && m.content === message.content && Math.abs(m.ts - message.ts) < 30)
      )) {
        pendingWsMessages.current[phone] = [...buf, message];
      }
    }

    // Skip contact list preview update for transcription, system_notice, tool_call, and error messages
    if (message.role === 'transcription' || message.role === 'system_notice' || message.role === 'tool_call' || message.role === 'error') return;

    setContacts(prev => {
      const idx = prev.findIndex(c => c.phone === phone);
      if (idx >= 0) {
        const updated = [...prev];
        const isUserMsg = message.role === 'user';
        const isViewing = phone === selectedRef.current && pageVisibleRef.current;
        let lastPreview = (message.content || '').substring(0, 80);
        if (message.media_type === 'image') lastPreview = message.content || '📷 Imagem';
        if (message.media_type === 'audio') lastPreview = '🎤 Áudio';
        updated[idx] = {
          ...updated[idx],
          last_message: lastPreview,
          last_message_role: message.role,
          last_message_ts: message.ts,
          last_message_status: message.status || '',
          last_message_msg_id: message.msg_id || '',
          msg_count: updated[idx].msg_count + 1,
          unread_count: isUserMsg && !isViewing
            ? (updated[idx].unread_count || 0) + 1
            : updated[idx].unread_count || 0,
          unread_ai_count: message.role === 'assistant' && !isViewing
            ? (updated[idx].unread_ai_count || 0) + 1
            : updated[idx].unread_ai_count || 0,
          updated_at: message.ts,
        };
        updated.sort((a, b) => (b.last_message_ts || b.updated_at || 0) - (a.last_message_ts || a.updated_at || 0));
        return updated;
      }
      fetchContacts(search);
      return prev;
    });
  }, [newMessage]);

  const messages = contactData ? contactData.messages || [] : [];
  const info = contactData ? contactData.info || {} : {};

  const autoReply = config ? config.auto_reply : false;
  const handleToggleAutoReply = useCallback(async (newValue) => {
    if (onConfigSave) {
      await onConfigSave({ auto_reply: newValue });
    }
  }, [onConfigSave]);

  return html`
    <div class="flex flex-col lg:flex-row h-full">
      <!-- Sidebar -->
      <div class="shrink-0 border-r border-wa-border transition-all duration-300 overflow-hidden ${sidebarHidden ? 'lg:w-0 lg:border-r-0' : 'lg:w-[400px]'} ${selected ? 'hidden lg:flex lg:flex-col' : 'flex flex-col w-full'}">
        <${ContactList}
          contacts=${contacts}
          loading=${loading}
          search=${search}
          onSearchChange=${handleSearchChange}
          selected=${selected}
          onSelect=${selectContact}
          onContextMenu=${setCtxMenu}
          typingState=${typingState}
          showArchived=${showArchived}
          onToggleArchived=${handleToggleArchived}
          globalTags=${globalTags}
          onStartConversation=${handleStartConversation}
          checkingPhone=${checkingPhone}
          checkPhoneError=${checkPhoneError}
          wsConnected=${wsConnected}
          autoReply=${autoReply}
          onToggleAutoReply=${handleToggleAutoReply}
        />
      </div>
      <!-- Toggle sidebar button (desktop only) -->
      <button
        class="hidden lg:flex items-center justify-center w-[14px] shrink-0 bg-wa-panel hover:bg-wa-hover border-r border-wa-border cursor-pointer transition-colors"
        onClick=${() => setSidebarHidden(h => !h)}
        title=${sidebarHidden ? 'Mostrar contatos' : 'Esconder contatos'}
      >
        <span class="text-wa-secondary text-[11px] select-none">${sidebarHidden ? '›' : '‹'}</span>
      </button>
      <!-- Chat panel -->
      <div class="flex-1 min-w-0 min-h-0 ${!selected ? 'hidden lg:flex' : 'flex'} relative">
        <div class="w-full h-full flex flex-col">
          ${loadingDetail
            ? html`<div class="flex items-center justify-center h-full bg-wa-panel text-wa-secondary animate-pulse-slow text-[14px]">Carregando...</div>`
            : html`<${ContactDetail}
                phone=${selected}
                onBack=${() => selectContact(null)}
                messages=${messages}
                setContactData=${setContactData}
                info=${info}
                contact=${contactData}
                onAvatarClick=${() => selected && setShowInfoPanel(true)}
                contactTyping=${selected && typingState[selected] || null}
                globalTags=${globalTags}
              />`
          }
          ${showInfoPanel && selected ? html`
            <${ContactInfoPanel}
              phone=${selected}
              info=${info}
              contactTags=${contactData && contactData.tags || []}
              globalTags=${globalTags}
              onGlobalTagsChange=${setGlobalTags}
              isGroup=${contactData && contactData.is_group}
              groupName=${contactData && contactData.group_name}
              onClose=${() => setShowInfoPanel(false)}
              onSave=${(updatedInfo, updatedTags) => {
                setContactData(prev => prev ? { ...prev, info: updatedInfo, tags: updatedTags } : prev);
                setContacts(prev => prev.map(c =>
                  c.phone === selected ? { ...c, name: updatedInfo.name || c.name, tags: updatedTags } : c
                ));
                setShowInfoPanel(false);
              }}
            />
          ` : null}
        </div>
      </div>
      ${ctxMenu ? html`
        <${ContextMenu}
          x=${ctxMenu.x}
          y=${ctxMenu.y}
          phone=${ctxMenu.phone}
          aiEnabled=${ctxMenu.aiEnabled}
          contactTags=${ctxMenu.tags}
          globalTags=${globalTags}
          isArchived=${ctxMenu.isArchived}
          onToggleAI=${handleToggleAI}
          onEditContact=${(phone) => { openInfoAfterSelect.current = true; selectContact(phone); }}
          onTagsUpdate=${(phone, newTags) => {
            setContacts(prev => prev.map(c => c.phone === phone ? { ...c, tags: newTags } : c));
            setCtxMenu(prev => prev && prev.phone === phone ? { ...prev, tags: newTags } : prev);
            if (phone === selectedRef.current) {
              setContactData(prev => prev ? { ...prev, tags: newTags } : prev);
            }
          }}
          onArchive=${handleArchive}
          onDelete=${handleDelete}
          onClose=${() => setCtxMenu(null)}
        />
      ` : null}
    </div>
  `;
}
