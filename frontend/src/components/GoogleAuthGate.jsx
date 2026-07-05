import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { apiUrl } from '../api/client';

export default function GoogleAuthGate({ children }) {
  const { t } = useTranslation();
  const [state, setState] = useState({
    loading: true,
    enabled: false,
    authenticated: true,
    user: null,
    error: '',
  });

  const loadStatus = async () => {
    try {
      const res = await fetch(apiUrl('/auth/status'), { credentials: 'include' });
      const data = await res.json();
      setState({
        loading: false,
        enabled: !!data.enabled,
        authenticated: !!data.authenticated,
        user: data.user || null,
        error: '',
      });
    } catch (e) {
      setState((prev) => ({
        ...prev,
        loading: false,
        error: e?.message || String(e),
      }));
    }
  };

  useEffect(() => {
    loadStatus();
    const onRequired = () => setState((prev) => ({ ...prev, enabled: true, authenticated: false }));
    window.addEventListener('mlac:login-required', onRequired);
    return () => window.removeEventListener('mlac:login-required', onRequired);
  }, []);

  if (state.loading) {
    return (
      <div className="remote-auth-gate" role="status">
        <div className="remote-auth-gate__card">
          <h2>{t('auth.checking', { defaultValue: 'Checking access…' })}</h2>
        </div>
      </div>
    );
  }

  if (!state.enabled || state.authenticated) return children;

  const login = () => {
    const returnTo = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    window.location.href = apiUrl(`/auth/google/login?return_to=${encodeURIComponent(returnTo)}`);
  };

  return (
    <div className="remote-auth-gate" role="dialog" aria-modal="true">
      <div className="remote-auth-gate__card">
        <h2>{t('auth.title', { defaultValue: 'Sign in required' })}</h2>
        <p>
          {t('auth.body', {
            defaultValue: 'Use a Google account that has been added to the activation list.',
          })}
        </p>
        {state.error ? <p role="alert">{state.error}</p> : null}
        <button type="button" onClick={login}>
          {t('auth.google', { defaultValue: 'Continue with Google' })}
        </button>
      </div>
    </div>
  );
}
