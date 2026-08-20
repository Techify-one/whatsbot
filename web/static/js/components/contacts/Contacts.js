import { h } from 'preact';
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import htm from 'htm';
import { getContacts, getContact, markAsRead, markAsUnread, toggleContactAI, getTags, deleteContact, archiveContact, pinContact, checkPhone, updateContactTags, createTag } from '../../services/api.js';
import { ContactList } from './ContactList.js';
import { ContactDetail } from './ContactDetail.js';
import { ContactInfoPanel } from './ContactInfoPanel.js';
import { ContextMenu } from './ContextMenu.js';
import { StartChatWarningModal } from './StartChatWarningModal.js';
import { formatPhoneDisplay } from './utils.js';

const html = htm.bind(h);

// ── Main Component ───────────────────────────────────────────────

export function Contacts({ newMessage, chatPresence, contactInfoUpdated, tagsChanged, contactTagsUpdated, contactAiToggled, messagesRead, messageStatus, messageAction, messageReaction, avatarUpdated, groupParticipantsChanged, initialContactId, wsConnected, config, onConfigSave, onUnreadChange }) {
  const [contacts, setContacts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(null);
  const [scrollToMsg, setScrollToMsg] = useState(null);  // DB id of a message to focus on open (search hit)
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
  const [startChatPhone, setStartChatPhone] = useState(null);  // pending 'start conversation' confirmation
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedPhones, setSelectedPhones] = useState([]);
  const pendingWsMessages = useRef({});
  const selectedRef = useRef(null);
  const typingTimers = useRef({});
  const contactsRef = useRef([]);
  const lastResolvedId = useRef(null);
  const pageVisibleRef = useRef(!document.hidden);

  // Keep refs in sync — avoids stale closures
  useEffect(() => { selectedRef.current = selected; }, [selected]);
  useEffect(() => { contactsRef.current = contacts; }, [contacts]);

  // Notify the app shell whenever the conversation list changes so it can refresh
  // the browser-tab unread badge — covers reads that fire no WS event (e.g. the
  // operator opening a chat on this same client).
  useEffect(() => { if (onUnreadChange) onUnreadChange(); }, [contacts]);

  // Track page visibility — mark selected contact as read when tab becomes visible
  useEffect(() => {
    const handler = () => {
      const visible = !document.hidden;
      pageVisibleRef.current = visible;
      if (visible && selectedRef.current) {
        markAsRead(selectedRef.current);
        setContacts(prev => prev.map(c =>
          c.phone === selectedRef.current ? { ...c, unread_count: 0, unread_ai_count: 0, has_unread_mention: false } : c
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

  const handleMarkUnread = useCallback(async (phone) => {
    const res = await markAsUnread(phone);
    if (res.ok) {
      setContacts(prev => prev.map(c =>
        c.phone === phone
          ? { ...c, unread_count: Math.max(c.unread_count || 0, 1) }
          : c
      ));
    }
  }, []);

  const handleMarkRead = useCallback(async (phone) => {
    const res = await markAsRead(phone);
    if (res.ok) {
      setContacts(prev => prev.map(c =>
        c.phone === phone ? { ...c, unread_count: 0, unread_ai_count: 0, has_unread_mention: false } : c
      ));
    }
  }, []);

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

  // Re-sort like the backend: pinned first, then by last message time desc.
  const sortContacts = useCallback((list) => {
    return [...list].sort((a, b) => {
      const ap = a.is_pinned ? 1 : 0;
      const bp = b.is_pinned ? 1 : 0;
      if (ap !== bp) return bp - ap;
      return (b.last_message_ts || b.updated_at || 0) - (a.last_message_ts || a.updated_at || 0);
    });
  }, []);

  const handlePin = useCallback(async (phone, pinned) => {
    const res = await pinContact(phone, pinned);
    if (res.ok) {
      setContacts(prev => sortContacts(prev.map(c =>
        c.phone === phone ? { ...c, is_pinned: res.data.pinned } : c
      )));
    }
  }, [sortContacts]);

  // ── Selection mode (bulk actions) ───────────────────────────────
  const enterSelection = useCallback(() => { setSelectionMode(true); setSelectedPhones([]); }, []);
  const exitSelection = useCallback(() => { setSelectionMode(false); setSelectedPhones([]); }, []);
  const toggleSelect = useCallback((phone) => {
    setSelectedPhones(prev => prev.includes(phone)
      ? prev.filter(p => p !== phone)
      : [...prev, phone]);
  }, []);
  const selectAllContacts = useCallback(() => {
    setSelectedPhones(contactsRef.current.map(c => c.phone));
  }, []);
  const clearSelection = useCallback(() => { setSelectedPhones([]); setSelectionMode(false); }, []);

  const handleBulkAI = useCallback(async (enabled) => {
    const phones = [...selectedPhones];
    if (!phones.length) return;
    await Promise.all(phones.map(p => toggleContactAI(p, enabled).catch(() => null)));
    setContacts(prev => prev.map(c =>
      phones.includes(c.phone) ? { ...c, ai_enabled: enabled } : c
    ));
    if (phones.includes(selectedRef.current)) {
      setContactData(prev => prev ? { ...prev, ai_enabled: enabled } : prev);
    }
  }, [selectedPhones]);

  const handleBulkArchive = useCallback(async () => {
    const phones = [...selectedPhones];
    if (!phones.length) return;
    const archived = !showArchivedRef.current; // archive when viewing inbox, unarchive when viewing archived
    await Promise.all(phones.map(p => archiveContact(p, archived).catch(() => null)));
    setContacts(prev => prev.filter(c => !phones.includes(c.phone)));
    if (phones.includes(selectedRef.current)) {
      setSelected(null);
      setContactData(null);
      history.pushState(null, '', '/');
    }
    exitSelection();
  }, [selectedPhones, exitSelection]);

  // Create a new global tag and add it to the sidebar's tag map. Returns true on
  // success so the caller (context menu / bulk menu) can then apply it.
  const handleCreateTag = useCallback(async (name, color) => {
    const res = await createTag(name, color);
    if (res.ok) {
      setGlobalTags(prev => ({ ...prev, [name]: { color } }));
      return true;
    }
    return false;
  }, []);

  // Apply a list of {phone, tags} results to the sidebar + open chat.
  const applyTagResults = useCallback((results) => {
    const map = Object.fromEntries(results.map(r => [r.phone, r.tags]));
    setContacts(prev => prev.map(c => map[c.phone] ? { ...c, tags: map[c.phone] } : c));
    if (map[selectedRef.current]) {
      setContactData(prev => prev ? { ...prev, tags: map[selectedRef.current] } : prev);
    }
  }, []);

  const _selectedTargets = useCallback(() => {
    const current = contactsRef.current;
    return [...selectedPhones].map(p => current.find(c => c.phone === p)).filter(Boolean);
  }, [selectedPhones]);

  // Toggle a tag across all selected: if every selected conversation already has
  // it, remove it from all; otherwise add it to all (keeping those that had it).
  // Repeated clicks cycle add → remove → add …
  const handleBulkTag = useCallback(async (tagName) => {
    const targets = _selectedTargets();
    if (!targets.length) return;
    const allHave = targets.every(c => (c.tags || []).includes(tagName));
    const results = await Promise.all(targets.map(async (c) => {
      const tags = Array.isArray(c.tags) ? c.tags : [];
      const next = allHave
        ? tags.filter(t => t !== tagName)
        : (tags.includes(tagName) ? tags : [...tags, tagName]);
      if (next.length === tags.length) return { phone: c.phone, tags };
      const res = await updateContactTags(c.phone, next).catch(() => null);
      return { phone: c.phone, tags: (res && res.ok) ? res.data.tags : tags };
    }));
    applyTagResults(results);
  }, [_selectedTargets, applyTagResults]);

  // Remove all tags from all selected conversations.
  const handleBulkRemoveAllTags = useCallback(async () => {
    const targets = _selectedTargets();
    if (!targets.length) return;
    const results = await Promise.all(targets.map(async (c) => {
      const tags = Array.isArray(c.tags) ? c.tags : [];
      if (!tags.length) return { phone: c.phone, tags };
      const res = await updateContactTags(c.phone, []).catch(() => null);
      return { phone: c.phone, tags: (res && res.ok) ? res.data.tags : [] };
    }));
    applyTagResults(results);
  }, [_selectedTargets, applyTagResults]);

  // Pin/unpin all selected at once (pinned ones sort to the top).
  const handleBulkPin = useCallback(async (pinned) => {
    const phones = [...selectedPhones];
    if (!phones.length) return;
    await Promise.all(phones.map(p => pinContact(p, pinned).catch(() => null)));
    setContacts(prev => sortContacts(prev.map(c =>
      phones.includes(c.phone) ? { ...c, is_pinned: pinned } : c
    )));
  }, [selectedPhones, sortContacts]);

  const handleBulkMarkRead = useCallback(async () => {
    const phones = [...selectedPhones];
    if (!phones.length) return;
    await Promise.all(phones.map(p => markAsRead(p).catch(() => null)));
    setContacts(prev => prev.map(c =>
      phones.includes(c.phone) ? { ...c, unread_count: 0, unread_ai_count: 0, has_unread_mention: false } : c
    ));
  }, [selectedPhones]);

  const handleBulkMarkUnread = useCallback(async () => {
    const phones = [...selectedPhones];
    if (!phones.length) return;
    await Promise.all(phones.map(p => markAsUnread(p).catch(() => null)));
    setContacts(prev => prev.map(c =>
      phones.includes(c.phone) ? { ...c, unread_count: Math.max(c.unread_count || 0, 1) } : c
    ));
  }, [selectedPhones]);

  // Push URL when selecting/deselecting a contact
  const selectContact = useCallback((phone, msgId = null) => {
    setScrollToMsg(msgId != null ? msgId : null);
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

  // Clicking "start conversation" no longer opens the chat right away: starting
  // a chat from an unofficial API is what most often gets a number banned, so we
  // warn first and offer to send the wa.me link to the operator's own phone.
  const handleStartConversation = useCallback((normalizedPhone) => {
    if (!normalizedPhone || checkingPhone) return;
    setCheckPhoneError(null);
    setStartChatPhone(normalizedPhone);
  }, [checkingPhone]);

  const confirmStartConversation = useCallback(async (normalizedPhone) => {
    if (!normalizedPhone || checkingPhone) return;
    setStartChatPhone(null);

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

  // Reload when archive filter changes (and drop any active selection)
  useEffect(() => { fetchContacts(search); setSelectionMode(false); setSelectedPhones([]); }, [showArchived]);

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
        c.phone === selected ? { ...c, unread_count: 0, unread_ai_count: 0, has_unread_mention: false } : c
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
      // WhatsApp emits a single `composing` event (not heartbeated). Auto-clear after
      // 25s as a defensive fallback in case `paused` never arrives (e.g. dropped connection).
      clearTimeout(typingTimers.current[phone]);
      typingTimers.current[phone] = setTimeout(() => {
        setTypingState(prev => { const n = { ...prev }; delete n[phone]; return n; });
      }, 25000);
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
        ? { ...c, unread_count: 0, ...(only_user ? {} : { unread_ai_count: 0, has_unread_mention: false }) }
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

  // Handle message deletions/revocations (from this panel, the phone, or the contact)
  useEffect(() => {
    if (!messageAction) return;
    const { action, phone, msg_id, db_id } = messageAction;
    if (phone && phone !== selectedRef.current) return;
    // Both revoke and "delete for me" keep the message in the list (and its content);
    // we only flag it as revoked so it renders with a scope-specific 'deleted'
    // indicator. action 'deleted' => "para mim"; 'revoked' => "para todos".
    const scope = action === 'deleted' ? 'me' : 'all';
    setContactData(prev => {
      if (!prev || !prev.messages) return prev;
      let changed = false;
      const updated = prev.messages.map(m => {
        if (((msg_id && m.msg_id === msg_id) || (db_id && (m._id === db_id || m.id === db_id))) && !m.revoked) {
          changed = true;
          return { ...m, revoked: true, revoke_scope: scope };
        }
        return m;
      });
      return changed ? { ...prev, messages: updated } : prev;
    });
  }, [messageAction]);

  // Handle live avatar updates (background sweep / opening a conversation
  // detected a changed photo) — bump avatar_v so the <img> re-fetches.
  useEffect(() => {
    if (!avatarUpdated) return;
    const { phone, v } = avatarUpdated;
    if (!phone || !v) return;
    setContacts(prev => prev.map(c => c.phone === phone ? { ...c, avatar_v: v } : c));
    setContactData(prev => (prev && prev.phone === phone) ? { ...prev, avatar_v: v } : prev);
  }, [avatarUpdated]);

  // Handle reaction updates (from this panel, the phone, or the contact)
  useEffect(() => {
    if (!messageReaction) return;
    const { phone, msg_id, reactions } = messageReaction;
    if (phone && phone !== selectedRef.current) return;
    setContactData(prev => {
      if (!prev || !prev.messages) return prev;
      let changed = false;
      const updated = prev.messages.map(m => {
        if (msg_id && m.msg_id === msg_id) {
          changed = true;
          return { ...m, reactions: (reactions && Object.keys(reactions).length) ? reactions : undefined };
        }
        return m;
      });
      return changed ? { ...prev, messages: updated } : prev;
    });
  }, [messageReaction]);

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
        // Reconcile by GOWA msg_id first: a plugin may have rewritten the text
        // (e.g. appended a signature), so an optimistic/prior copy with the same
        // msg_id won't match by content — adopt the server's text in place
        // instead of appending a duplicate.
        if (message.msg_id && prev.messages) {
          const byId = prev.messages.findIndex(m => m.msg_id === message.msg_id);
          if (byId !== -1) {
            const updated = [...prev.messages];
            updated[byId] = {
              ...updated[byId],
              content: message.content != null ? message.content : updated[byId].content,
              status: message.status || updated[byId].status,
              _status: null,
            };
            return { ...prev, messages: updated };
          }
        }
        // Deduplicate by ts + role, or by content + role (within 30s window)
        const dupIdx = prev.messages ? prev.messages.findIndex(m =>
          (m.ts === message.ts && m.role === message.role) ||
          (m.role === message.role && m.content === message.content && Math.abs(m.ts - message.ts) < 30)
        ) : -1;
        if (dupIdx !== -1) {
          // Merge ids/status from server into existing (optimistic) message
          if (message.msg_id || message.status || message._id) {
            const updated = [...prev.messages];
            updated[dupIdx] = { ...updated[dupIdx],
              ...(message.msg_id ? { msg_id: message.msg_id } : {}),
              ...(message._id && !updated[dupIdx]._id ? { _id: message._id } : {}),
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
        else if (message.media_type === 'audio') lastPreview = '🎤 Áudio';
        else if (message.media_type === 'video') lastPreview = message.content || '🎥 Vídeo';
        else if (message.media_type === 'sticker') lastPreview = '🎨 Sticker';
        else if (message.media_type === 'document') lastPreview = message.content || '📄 Documento';
        else if (message.media_type === 'location') lastPreview = message.content || '📍 Localização';
        else if (message.media_type === 'live_location') lastPreview = '📍 Localização ao vivo';
        else if (message.media_type === 'poll') lastPreview = message.content || '📊 Enquete';
        else if (message.media_type === 'interactive') lastPreview = message.content || '↩️ Resposta';
        else if (message.media_type === 'order') lastPreview = message.content || '🛒 Pedido';
        else if (message.media_type === 'product') lastPreview = '🏷️ Produto';
        else if (message.media_type === 'contact' || message.media_type === 'contacts') lastPreview = message.content || '👤 Contato';
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
          has_unread_mention: (message.mentioned && !isViewing)
            ? true
            : (updated[idx].has_unread_mention || false),
          updated_at: message.ts,
        };
        return sortContacts(updated);
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
          selectionMode=${selectionMode}
          selectedPhones=${selectedPhones}
          onEnterSelection=${enterSelection}
          onExitSelection=${exitSelection}
          onToggleSelect=${toggleSelect}
          onSelectAll=${selectAllContacts}
          onCreateTag=${handleCreateTag}
          onClearSelection=${clearSelection}
          onBulkAI=${handleBulkAI}
          onBulkArchive=${handleBulkArchive}
          onBulkTag=${handleBulkTag}
          onBulkRemoveAllTags=${handleBulkRemoveAllTags}
          onBulkPin=${handleBulkPin}
          onBulkMarkRead=${handleBulkMarkRead}
          onBulkMarkUnread=${handleBulkMarkUnread}
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
                groupParticipantsChanged=${groupParticipantsChanged}
                scrollToMsg=${scrollToMsg}
                onScrolledToMsg=${() => setScrollToMsg(null)}
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
              avatarV=${contactData && contactData.avatar_v}
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
          isUnread=${ctxMenu.isUnread}
          isPinned=${ctxMenu.isPinned}
          onToggleAI=${handleToggleAI}
          onEditContact=${(phone) => {
            if (selectedRef.current === phone) {
              // Already open — the [selected] effect won't refire, so open directly.
              setShowInfoPanel(true);
            } else {
              openInfoAfterSelect.current = true;
              selectContact(phone);
            }
          }}
          onMarkUnread=${handleMarkUnread}
          onMarkRead=${handleMarkRead}
          onTagsUpdate=${(phone, newTags) => {
            setContacts(prev => prev.map(c => c.phone === phone ? { ...c, tags: newTags } : c));
            setCtxMenu(prev => prev && prev.phone === phone ? { ...prev, tags: newTags } : prev);
            if (phone === selectedRef.current) {
              setContactData(prev => prev ? { ...prev, tags: newTags } : prev);
            }
          }}
          onArchive=${handleArchive}
          onPin=${handlePin}
          onDelete=${handleDelete}
          onCreateTag=${handleCreateTag}
          onClose=${() => setCtxMenu(null)}
        />
      ` : null}
      ${startChatPhone ? html`
        <${StartChatWarningModal}
          phone=${startChatPhone}
          phoneDisplay=${formatPhoneDisplay(startChatPhone)}
          onConfirm=${() => confirmStartConversation(startChatPhone)}
          onClose=${() => setStartChatPhone(null)}
        />
      ` : null}
    </div>
  `;
}
