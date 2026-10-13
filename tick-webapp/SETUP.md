# One-click ✓ Mark done (optional, about 3 minutes, once)

By default, ✓ Mark done opens your mail client with a pre-filled email to yourself, and you press send.
This optional web app makes it one click: the link opens a browser tab that says "Marked done ✓",
and the web app sends that same email for you. Sage reads ticks exactly as before, so nothing else changes.

It runs as a Google Apps Script under **your own** Google account. Only you can deploy it, because it
needs your permission to send mail as you. It only ever emails you, at your own address.

1. Go to https://script.google.com, signed in with your work Google account, and click **New project**.
2. Delete the starter code, paste in everything from `Code.gs` (this folder), and rename the project **Sage tick**.
3. **Deploy → New deployment**. Click the cog next to "Select type" and choose **Web app**.
   - Execute as: **Me**
   - Who has access: **Only myself**. Colleagues who can read your blocks then can't tick them. The script
     also checks that whoever opens the link is you, in case the access setting is ever widened.
4. Click **Deploy**, then **Authorize access** and approve. It asks for permission to send email as you.
5. Copy the **Web app URL** (it ends in `/exec`) and give it to Claude ("here's my Sage tick URL"), or paste it
   into `done_url` in `~/Documents/Sage/config.json` yourself.

Sage only accepts `https://script.google.com/` URLs here. Anything else falls back to the mailto link.

**Test it** by opening `<your URL>?k=test:sage-tick&t=Test`. You should see "Marked done ✓ Test" and get a
`Sage done: test:sage-tick` email. Sage ignores `test:` keys, so this ticks nothing off.

The next Sage run rewrites every open block so its ✓ links use the web app. That's one round of
calendar updates, with notifications off.

**"Sorry, unable to open the file at this time":** you're signed into more than one Google account
and the browser picked the wrong one. Open the link in a window where only your work account is signed in.

**Changing Code.gs later:** use Deploy → Manage deployments → edit (pencil) → Version: New version.
That keeps the same URL. A brand new deployment gets a new URL, which you'd need to give Sage again.

**Undo:** clear `done_url` in `config.json` (the next run switches links back to mailto), then archive
the deployment in Apps Script.
