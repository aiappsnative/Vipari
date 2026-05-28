(() => {
    const cookieKey = 'driftguard-theme';
    const storageKey = 'driftguard-theme';
    const root = document.documentElement;

    const toggleButtons = Array.from(document.querySelectorAll('[data-theme-toggle]'));
    const media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

    const getStoredTheme = () => {
        try {
            return window.localStorage.getItem(storageKey);
        } catch {
            return null;
        }
    };

    const storeTheme = (theme) => {
        try {
            window.localStorage.setItem(storageKey, theme);
        } catch {
            return;
        }
    };

    const storeThemeCookie = (theme) => {
        document.cookie = `${cookieKey}=${theme}; path=/; max-age=31536000; SameSite=Lax`;
    };

    const preferredTheme = () => {
        const stored = getStoredTheme();
        if (stored === 'dark' || stored === 'light') {
            return stored;
        }
        const rootTheme = root.getAttribute('data-theme');
        if (rootTheme === 'dark' || rootTheme === 'light') {
            return rootTheme;
        }
        const bodyTheme = document.body?.getAttribute('data-theme');
        if (bodyTheme === 'dark' || bodyTheme === 'light') {
            return bodyTheme;
        }
        return media && media.matches ? 'dark' : 'light';
    };

    const syncThemeInputs = (theme) => {
        const radio = document.querySelector(`input[name="theme_preference"][value="${theme}"]`);
        if (radio instanceof HTMLInputElement) {
            radio.checked = true;
        }
    };

    const applyTheme = (theme, persist = true) => {
        root.setAttribute('data-theme', theme);
        if (document.body) {
            document.body.setAttribute('data-theme', theme);
        }
        if (persist) {
            storeTheme(theme);
            storeThemeCookie(theme);
        }
        syncThemeInputs(theme);
        toggleButtons.forEach((button) => {
            button.setAttribute('aria-pressed', String(theme === 'dark'));
            button.setAttribute('aria-label', theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
            const label = button.querySelector('[data-theme-toggle-label]');
            if (label) {
                label.textContent = theme === 'dark' ? 'Dark mode' : 'Light mode';
            }
            const glyph = button.querySelector('[data-theme-toggle-glyph]');
            if (glyph) {
                glyph.textContent = theme === 'dark' ? '☀' : '☾';
            }
        });
    };

    toggleButtons.forEach((button) => {
        button.addEventListener('click', () => {
            applyTheme(root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
        });
    });

    document.querySelectorAll('input[name="theme_preference"]').forEach((input) => {
        input.addEventListener('change', (event) => {
            const nextTheme = event.target instanceof HTMLInputElement ? event.target.value : null;
            if (nextTheme === 'dark' || nextTheme === 'light') {
                applyTheme(nextTheme);
            }
        });
    });

    applyTheme(preferredTheme(), false);
    storeThemeCookie(preferredTheme());
})();