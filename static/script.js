/* =========================================================
   TRAVELLER STOP - CUSTOM JAVASCRIPT
   Modern, Clean, High Performance & Mobile Friendly
   Updated: May 2026
========================================================= */

/**
 * Search Functionality
 */
function searchRooms() {
    const locationInput = document.querySelector('input[name="location"]');
    if (!locationInput) return;

    const location = locationInput.value.trim();
    
    if (location) {
        window.location.href = `/rooms?location=${encodeURIComponent(location)}`;
    } else {
        window.location.href = '/rooms';
    }
}

/**
 * Smooth Scroll for Anchor Links
 */
function initSmoothScroll() {
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            const targetId = this.getAttribute('href');
            if (targetId === '#') return;

            const targetElement = document.querySelector(targetId);
            if (targetElement) {
                e.preventDefault();
                targetElement.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        });
    });
}

/**
 * Active Navigation Highlight
 */
function highlightActiveNav() {
    const currentPath = window.location.pathname;
    const navLinks = document.querySelectorAll('.nav-link');

    navLinks.forEach(link => {
        link.classList.remove('active', 'fw-bold');

        const linkHref = link.getAttribute('href');
        
        if (linkHref === currentPath || 
            (currentPath === '/' && (linkHref === '/' || linkHref === ''))) {
            link.classList.add('active', 'fw-bold');
        }
    });
}

/**
 * Auto Hide Flash Messages
 */
function initFlashMessages() {
    const alerts = document.querySelectorAll('.alert');
    
    alerts.forEach((alert, index) => {
        setTimeout(() => {
            alert.style.transition = 'all 0.5s ease-out';
            alert.style.opacity = '0';
            alert.style.transform = 'translateY(-20px)';
            
            setTimeout(() => {
                if (alert.parentNode) alert.parentNode.removeChild(alert);
            }, 600);
        }, 4500 + (index * 400));
    });
}

/**
 * Navbar Scroll Effect
 */
function initNavbarScroll() {
    const navbar = document.querySelector('.navbar');
    if (!navbar) return;

    let lastScroll = 0;

    window.addEventListener('scroll', () => {
        const currentScroll = window.pageYOffset;

        if (currentScroll > lastScroll && currentScroll > 120) {
            navbar.style.transform = 'translateY(-100%)';
        } else {
            navbar.style.transform = 'translateY(0)';
        }
        lastScroll = currentScroll;
    });
}

/**
 * Image Error Handler (Fallback)
 */
function initImageFallback() {
    document.querySelectorAll('img').forEach(img => {
        img.addEventListener('error', function () {
            this.src = 'https://source.unsplash.com/random/800x600/?room,interior';
            this.onerror = null; // Prevent infinite loop
        });
    });
}

/**
 * Booking Date Validation
 */
function initBookingDates() {
    // Handle specific IDs (Room Detail) and Search Names (Landing Page)
    const checkins = [document.getElementById('checkin'), document.querySelector('input[name="checkin"]')];
    const checkouts = [document.getElementById('checkout'), document.querySelector('input[name="checkout"]')];

    const today = new Date().toISOString().split('T')[0];

    checkins.forEach((cin, idx) => {
        const cout = checkouts[idx];
        if (cin && cout) {
            cin.min = today;
            cin.addEventListener('change', () => {
                if (cin.value) {
                    cout.min = cin.value;
                }
            });
        }
    });
}

/**
 * Image Preview for File Uploads
 */
function initImagePreview() {
    const fileInputs = document.querySelectorAll('input[type="file"]');
    
    fileInputs.forEach(input => {
        input.addEventListener('change', function() {
            const previewId = this.getAttribute('data-preview');
            if (!previewId) return;

            const previewContainer = document.getElementById(previewId);
            if (!previewContainer) return;

            const file = this.files[0];
            if (file) {
                const reader = new FileReader();
                reader.onload = function(e) {
                    previewContainer.innerHTML = `<img src="${e.target.result}" class="img-fluid rounded-3" style="max-height: 280px;">`;
                    previewContainer.style.display = 'block';
                }
                reader.readAsDataURL(file);
            }
        });
    });
}

/**
 * Scroll Reveal Animation
 */
function initScrollReveal() {
    const revealElements = document.querySelectorAll('.info-card, .policy-section, .service-card, .contact-info-item');
    
    const reveal = () => {
        revealElements.forEach(el => {
            const elementTop = el.getBoundingClientRect().top;
            const elementVisible = 150;
            if (elementTop < window.innerHeight - elementVisible) {
                el.classList.add('animate', 'slideIn');
                el.style.opacity = '1';
            }
        });
    };

    window.addEventListener('scroll', reveal);
    reveal(); // Initial check
}

/**
 * Initialize All Features
 */
document.addEventListener('DOMContentLoaded', function () {
    
    highlightActiveNav();
    initSmoothScroll();
    initFlashMessages();
    initNavbarScroll();
    initImageFallback();
    initBookingDates();
    initImagePreview();
    initScrollReveal();

    // Keyboard Shortcut: Ctrl/Cmd + K → Focus Search
    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
            e.preventDefault();
            const searchInput = document.querySelector('input[name="location"]');
            if (searchInput) {
                searchInput.focus();
                searchInput.select();
            }
        }
    });

    // Console Signature
    console.log('%c🚀 Traveller Stop Premium Loaded Successfully ✨', 
                'color: #e63946; font-size: 16px; font-weight: bold; letter-spacing: 1px;');

    // Add scrolled class to navbar on scroll
    const navbar = document.querySelector('.navbar');
    if (navbar) {
        window.addEventListener('scroll', () => {
            if (window.scrollY > 50) {
                navbar.classList.add('scrolled');
            } else {
                navbar.classList.remove('scrolled');
            }
        });
    }
});