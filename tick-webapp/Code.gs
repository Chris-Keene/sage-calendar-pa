/**
 * Sage one-click tick. With done_url set, every "✓ Mark done" link in a Sage block opens
 * this web app, which sends the same "Sage done: <key>" email the mailto link would, so the
 * engine reads ticks exactly as before. Deploy: Execute as Me, Who has access: Only myself.
 * See SETUP.md in this folder.
 */
const KEY_RE = /^[A-Za-z0-9:_.\/\-]{1,200}$/;  // every key Sage writes fits this

function doGet(e) {
  const me = Session.getEffectiveUser().getEmail();
  const visitor = Session.getActiveUser().getEmail();
  // Belt and braces: "Only myself" already blocks colleagues, but a deployment widened
  // to the whole domain would otherwise let anyone who can read the block tick it.
  if (!visitor || visitor.toLowerCase() !== me.toLowerCase()) {
    return page_('Not yours to tick', 'Only the calendar owner can mark Sage tasks done. Nothing was changed.', false);
  }
  const key = String(e.parameter.k || '').trim();
  if (!KEY_RE.test(key)) {
    return page_('That link looks broken', 'No task key came through, so nothing was marked done.', false);
  }
  const title = String(e.parameter.t || '').slice(0, 120);
  // A reload or a double tap shouldn't send a second email.
  const cache = CacheService.getUserCache();
  if (!cache.get('tick:' + key)) {
    GmailApp.sendEmail(me, 'Sage done: ' + key,
      'Ticked from the calendar' + (title ? ': ' + title : '') + '.\nSage clears it on its next run.');
    cache.put('tick:' + key, '1', 21600);
  }
  return page_('Marked done ✓', (title ? title + '. ' : '') +
    'Sage clears it from your calendar on its next run. You can close this tab.', true);
}

function page_(heading, message, ok) {
  const esc = s => String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const html =
    '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>' + esc(heading) + ' · Sage</title>' +
    '<style>body{margin:0;font:16px/1.5 system-ui,-apple-system,sans-serif;background:#f6f8f5;color:#1f2a1f}' +
    'main{max-width:30rem;margin:15vh auto;padding:2rem;background:#fff;border-radius:12px;border-left:6px solid ' +
    (ok ? '#33b679' : '#8a6d00') + '}h1{margin:0 0 .5rem;font-size:1.5rem}p{margin:0;color:#3a463a}</style></head>' +
    '<body><main><h1>' + esc(heading) + '</h1><p>' + esc(message) + '</p></main></body></html>';
  return HtmlService.createHtmlOutput(html)
    .setTitle(heading + ' · Sage')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}
