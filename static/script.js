function initImageFallback() {
    const fallback = document.body.dataset.imageFallback;

    document.querySelectorAll('img').forEach(image => {
        const replaceImage = () => {
            if (fallback && image.src !== new URL(fallback, document.baseURI).href) {
                image.removeAttribute('srcset');
                image.src = fallback;
            }
        };

        image.addEventListener('error', replaceImage, { once: true });
        if (image.getAttribute('src') && image.complete && !image.naturalWidth) {
            replaceImage();
        }
    });
}

function toDateInputValue(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return [year, month, day].join('-');
}

function initBookingDates() {
    const checkin = document.getElementById('checkin') || document.getElementById('search-checkin');
    const checkout = document.getElementById('checkout') || document.getElementById('search-checkout');
    if (!checkin || !checkout) return;

    const today = new Date();
    const estimate = document.querySelector('.stay-estimate');
    checkin.min = toDateInputValue(today);

    const updateCheckout = () => {
        const earliest = checkin.value ? new Date(checkin.value + 'T00:00:00') : new Date(today);
        earliest.setDate(earliest.getDate() + 1);
        checkout.min = toDateInputValue(earliest);
        if (checkout.value && checkout.value < checkout.min) {
            checkout.value = '';
        }
    };

    const updateEstimate = () => {
        if (!estimate) return;
        const nights = (Date.parse(checkout.value) - Date.parse(checkin.value)) / 86400000;
        estimate.hidden = !(nights > 0);
        if (estimate.hidden) return;

        const locale = document.documentElement.lang === 'bn' ? 'bn-BD' : 'en-BD';
        const money = new Intl.NumberFormat(locale, { style: 'currency', currency: 'BDT' });
        const count = new Intl.NumberFormat(locale);
        estimate.querySelector('[data-estimate-total]').textContent = money.format(nights * Number(estimate.dataset.rate));
        estimate.querySelector('[data-estimate-nights]').textContent =
            count.format(nights) + ' ' + (nights === 1 ? estimate.dataset.singular : estimate.dataset.plural);
    };

    checkin.addEventListener('change', () => {
        updateCheckout();
        updateEstimate();
    });
    checkout.addEventListener('change', updateEstimate);
    updateCheckout();
    updateEstimate();
}

function initImagePreview() {
    document.querySelectorAll('input[type="file"][data-preview]').forEach(input => {
        const preview = document.getElementById(input.dataset.preview);
        if (!preview) return;

        let previewUrl;
        input.addEventListener('change', () => {
            if (previewUrl) URL.revokeObjectURL(previewUrl);
            const file = input.files[0];
            if (!file) {
                preview.replaceChildren();
                preview.style.display = 'none';
                return;
            }

            previewUrl = URL.createObjectURL(file);
            const image = new Image();
            image.src = previewUrl;
            image.alt = 'Selected image preview';
            image.className = 'img-fluid rounded-3 upload-preview';
            preview.replaceChildren(image);
            preview.style.display = 'block';
        });
    });
}

document.addEventListener('DOMContentLoaded', () => {
    initImageFallback();
    initBookingDates();
    initImagePreview();
    initNearbySearch();
    initFavourites();
});

function initNearbySearch() {
    document.querySelectorAll('[data-nearby]').forEach(button => {
        button.addEventListener('click', () => {
            const panel = button.closest('.search-panel');
            const status = panel.querySelector('.nearby-status');
            if (!navigator.geolocation) {
                status.textContent = button.dataset.error;
                return;
            }

            button.disabled = true;
            status.textContent = button.dataset.loading;
            navigator.geolocation.getCurrentPosition(({ coords }) => {
                const form = panel.querySelector('form');
                const url = new URL(form.action);
                const { latitude, longitude } = coords;
                const fields = new FormData(form);
                url.searchParams.set('nearby', '1');
                url.searchParams.set('lat', latitude.toFixed(1));
                url.searchParams.set('lng', longitude.toFixed(1));
                for (const name of ['checkin', 'checkout']) {
                    if (fields.get(name)) url.searchParams.set(name, fields.get(name));
                }
                window.location.assign(url);
            }, () => {
                status.textContent = button.dataset.error;
                button.disabled = false;
            }, { enableHighAccuracy: false, timeout: 9000, maximumAge: 300000 });
        });
    });
}

function initFavourites() {
    const storageKey = 'traveller-stop-favourites';
    const buttons = [...document.querySelectorAll('[data-favourite]')];
    const grid = document.querySelector('[data-saved-grid]');
    const empty = document.querySelector('[data-saved-empty]');
    let saved = new Set();

    function readSaved() {
        try {
            const values = JSON.parse(localStorage.getItem(storageKey) || '[]');
            return new Set(Array.isArray(values) ? values.filter(id => typeof id === 'string') : []);
        } catch {
            return new Set();
        }
    }

    function render() {
        for (const button of buttons) {
            const active = saved.has(button.dataset.favourite);
            button.setAttribute('aria-pressed', String(active));
            button.setAttribute('aria-label', active ? button.dataset.removeLabel : button.dataset.saveLabel);
        }

        if (grid && empty) {
            let visible = 0;
            for (const room of grid.querySelectorAll('[data-room]')) {
                room.hidden = !saved.has(room.dataset.room);
                if (!room.hidden) visible++;
            }
            grid.hidden = visible === 0;
            empty.hidden = visible > 0;
        }
    }

    for (const button of buttons) {
        button.addEventListener('click', () => {
            const id = button.dataset.favourite;
            if (saved.has(id)) saved.delete(id);
            else saved.add(id);
            try {
                localStorage.setItem(storageKey, JSON.stringify([...saved]));
            } catch {
                // Keep the current page usable when browser storage is disabled.
            }
            render();
        });
    }

    window.addEventListener('storage', event => {
        if (event.key !== storageKey && event.key !== null) return;
        saved = readSaved();
        render();
    });

    saved = readSaved();
    render();
}
