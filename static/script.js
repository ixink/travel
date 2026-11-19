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
    const checkin = document.getElementById('checkin');
    const checkout = document.getElementById('checkout');
    if (!checkin || !checkout) return;

    const today = new Date();
    checkin.min = toDateInputValue(today);

    const updateCheckout = () => {
        const earliest = checkin.value ? new Date(checkin.value + 'T00:00:00') : new Date(today);
        earliest.setDate(earliest.getDate() + 1);
        checkout.min = toDateInputValue(earliest);
        if (checkout.value && checkout.value < checkout.min) {
            checkout.value = '';
        }
    };

    checkin.addEventListener('change', updateCheckout);
    updateCheckout();
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
});
