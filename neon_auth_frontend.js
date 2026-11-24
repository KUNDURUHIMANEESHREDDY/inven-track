import { createAuthClient } from '@neon/auth';

// Managed Better Auth runs on this Inventrack Neon branch. This URL is public.
const auth = createAuthClient('https://ep-orange-sound-bisa5n8y.neonauth.c-5.ap-southeast-1.aws.neon.tech/inven_track/auth');
const el = (id) => document.getElementById(id);
const status = el('neon-status');
const form = el('neon-auth-form');
const submit = el('neon-submit');
let mode = 'signup';

function message(text, failure = false) {
  status.textContent = text;
  status.className = failure ? 'notice error' : 'notice';
  status.hidden = false;
}
function setBusy(busy) {
  submit.disabled = busy;
  submit.textContent = busy ? 'Working...' : mode === 'signup' ? 'Create account' : 'Sign in';
}
async function displayCurrentUser() {
  const { data, error } = await auth.getSession();
  if (error) { message(error.message || 'Could not read the session.', true); return; }
  const user = data?.user;
  el('current-user').textContent = user ? 'Signed in as ' + user.name + ' (' + user.email + ')' : 'Not signed in';
  el('signout').hidden = !user;
  if (user) message('Neon Auth session verified. Your account is active.');
}
el('mode-signup').addEventListener('click', () => {
  mode = 'signup'; form.reset(); el('full-name-wrap').hidden = false; el('full-name').required = true;
  el('mode-signup').setAttribute('aria-selected', 'true');
  el('mode-signin').setAttribute('aria-selected', 'false');
  status.hidden = true; setBusy(false);
});
el('mode-signin').addEventListener('click', () => {
  mode = 'signin'; form.reset(); el('full-name-wrap').hidden = true; el('full-name').required = false;
  el('mode-signup').setAttribute('aria-selected', 'false');
  el('mode-signin').setAttribute('aria-selected', 'true');
  status.hidden = true; setBusy(false);
});
form.addEventListener('submit', async (event) => {
  event.preventDefault(); setBusy(true); status.hidden = true;
  const email = el('email').value.trim();
  const password = el('password').value;
  try {
    const result = mode === 'signup'
      ? await auth.signUp.email({ email, password, name: el('full-name').value.trim() })
      : await auth.signIn.email({ email, password });
    if (result.error) throw new Error(result.error.message || 'Authentication failed.');
    await displayCurrentUser();
  } catch (error) {
    message(error.message || 'Could not complete authentication.', true);
  } finally { setBusy(false); }
});
el('check-session').addEventListener('click', () => displayCurrentUser().catch(e => message(e.message,true)));
el('signout').addEventListener('click', async () => {
  const { error } = await auth.signOut();
  if (error) message(error.message || 'Could not sign out.',true);
  else { message('Signed out.'); await displayCurrentUser(); }
});
displayCurrentUser().catch(e => message(e.message,true));
