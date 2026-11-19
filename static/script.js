function initImageFallback() {
    const fallback = document.body.dataset.imageFallback;

    document.querySelectorAll('img:not([data-gallery-image])').forEach(image => {
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
    initGallery();
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

function initGallery() {
    const gallery = document.querySelector('[data-gallery]');
    if (!gallery) return;

    const slides = [...gallery.querySelectorAll('[data-slide]')];
    if (slides.length < 2) return;

    const image = gallery.querySelector('[data-gallery-image]');
    const caption = gallery.querySelector('[data-gallery-caption]');
    const toggle = gallery.querySelector('[data-gallery-toggle]');
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const failedSlides = new Set();
    let index = 0;
    let requestedIndex = 0;
    let requestNumber = 0;
    let playing = !motion.matches;
    let timer;

    function schedule() {
        clearTimeout(timer);
        if (playing && !document.hidden && !gallery.matches(':hover, :focus-within')) {
            timer = setTimeout(() => move(1), 6500);
        }
    }

    function setPlaying(value) {
        playing = value;
        toggle.setAttribute('aria-label', gallery.dataset[value ? 'pause' : 'play']);
        toggle.firstElementChild.className = value ? 'fas fa-pause' : 'fas fa-play';
        schedule();
    }

    async function move(direction) {
        clearTimeout(timer);
        const sequence = ++requestNumber;
        let next = requestedIndex;
        for (let count = 0; count < slides.length; count++) {
            next = (next + direction + slides.length) % slides.length;
            if (next === index || failedSlides.has(next)) continue;
            requestedIndex = next;
            const slide = slides[next];
            const preload = new Image();
            preload.src = slide.dataset.src;
            try {
                await preload.decode();
            } catch {
                failedSlides.add(next);
                if (sequence !== requestNumber) return;
                continue;
            }

            if (sequence !== requestNumber) return;
            index = next;
            image.src = slide.dataset.src;
            image.alt = slide.dataset.alt;
            caption.textContent = slide.dataset.caption;
            if (!motion.matches) {
                image.animate([{ opacity: .55 }, { opacity: 1 }], { duration: 360 });
            }
            schedule();
            return;
        }
        requestedIndex = index;
        setPlaying(false);
    }

    gallery.querySelector('[data-gallery-controls]').hidden = false;
    gallery.querySelector('[data-gallery-prev]').addEventListener('click', () => move(-1));
    gallery.querySelector('[data-gallery-next]').addEventListener('click', () => move(1));
    toggle.addEventListener('click', () => setPlaying(!playing));
    for (const event of ['mouseenter', 'mouseleave', 'focusin', 'focusout']) {
        gallery.addEventListener(event, () => setTimeout(schedule, 0));
    }
    document.addEventListener('visibilitychange', schedule);
    motion.addEventListener('change', event => {
        if (event.matches) setPlaying(false);
    });
    const recoverImage = () => {
        failedSlides.add(index);
        move(1);
    };
    image.addEventListener('error', recoverImage);
    setPlaying(playing);
    if (image.complete && !image.naturalWidth) recoverImage();
}
