---
id: PERF-023
title: ChatMessage re-renderiza todos los mensajes y re-parsea markdown en cada token del stream
category: performance
impact: high
effort: S
risk: low
status: todo
files:
  - frontend/src/components/chat/ChatMessage.tsx:29
  - frontend/src/components/chat/ChatMessage.tsx:56
  - frontend/src/components/chat/ChatPanel.tsx:312-314
commits: []
created: 2026-06-10
---

## Problema
`ChatPanel.tsx:312-314` mapea `activeSlot.messages` a `<ChatMessageView key={msg.id}>`. Durante
el streaming, el handler de `text_chunk`/`thought_chunk`/`tool_call` en `useChatSocket` construye
un nuevo array `messages` en cada token (`const msgs = [...s.messages]; msgs[idx] = {...}`), así
que la identidad del array cambia cada tick mientras solo el mensaje in-flight es reemplazado
(los completados mantienen identidad estable). `ChatMessageView` (`ChatMessage.tsx:29`) es un
componente función plano (sin memo) que corre `ReactMarkdown` + `remarkGfm`
(`ChatMessage.tsx:56`) sobre `message.text` en cada render. Como el padre re-renderiza toda la
lista y los hijos no están memoizados, **todos los mensajes previos re-parsean markdown en cada
token** de la respuesta en curso → jank creciente en conversaciones largas.

## Solución propuesta
Envolver `ChatMessageView` en `React.memo` para que solo re-renderice cuando su prop (el objeto
`message`) realmente cambia — los mensajes completados conservan identidad y se saltan.
Opcionalmente memoizar el render de `ReactMarkdown` con `useMemo` keyed sobre `message.text`
para que solo ese nodo re-parsee. `useChatSocket` ya preserva la identidad de los mensajes
no modificados.

## Criterio de aceptación
- [ ] `ChatMessageView` está envuelto en `React.memo`
- [ ] Durante el streaming, solo el mensaje in-flight re-renderiza (verificado con React DevTools Profiler: los previos no re-renderizan por token)
- [ ] El output de markdown de los mensajes completados es idéntico

## Notas
Cada `ChatMessageView` depende solo de su prop `message`, así que la memoización es segura.
Distinto de [[CORR-009]] (setState impuro en `useChatSocket`).
