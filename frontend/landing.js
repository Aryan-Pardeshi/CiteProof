/* ==========================================================================
   DEEPRESEARCH MARKETING LANDING PAGE LOGIC (APPLE MOTION & INTERACTION)
   ========================================================================== */

document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    initGitHubStars();
    initInspectorTabs();
    initExampleGallery();
    initSmoothScroll();
    initMobileNav();
    initScrollAnimations();
    initNavbarScroll();
    initCardSpecularLighting();

    if (window.lucide && typeof window.lucide.createIcons === 'function') {
        window.lucide.createIcons();
    }
});

/* ==========================================================================
   1. LIVE GITHUB STARS FETCHER & CACHE
   ========================================================================== */
const GITHUB_REPO_API = 'https://api.github.com/repos/Aryan-Pardeshi/CiteProof';
const GITHUB_STARS_CACHE_KEY = 'deepresearch_github_stars_v3';
const GITHUB_STARS_CACHE_TTL_MS = 60 * 60 * 1000; // 1 hour

async function initGitHubStars() {
    const starBadges = document.querySelectorAll('.gh-star-count-badge');
    if (!starBadges.length) return;

    function renderStarCount(count) {
        const formatted = count >= 1000 ? `${(count / 1000).toFixed(1)}k` : String(count);
        starBadges.forEach(badge => {
            badge.textContent = formatted;
            badge.style.display = 'inline-flex';
        });
    }

    // 1. Check local cache first
    try {
        const cached = localStorage.getItem(GITHUB_STARS_CACHE_KEY);
        if (cached) {
            const parsed = JSON.parse(cached);
            if (parsed && typeof parsed.count === 'number' && (Date.now() - (parsed.timestamp || 0)) < GITHUB_STARS_CACHE_TTL_MS) {
                renderStarCount(parsed.count);
                return;
            }
        }
    } catch (e) {
        console.warn('LocalStorage error reading GitHub stars cache:', e);
    }

    // 2. Fetch live count from GitHub API
    try {
        const response = await fetch(GITHUB_REPO_API);
        if (response.ok) {
            const data = await response.json();
            if (data && typeof data.stargazers_count === 'number') {
                renderStarCount(data.stargazers_count);
                try {
                    localStorage.setItem(GITHUB_STARS_CACHE_KEY, JSON.stringify({
                        count: data.stargazers_count,
                        timestamp: Date.now()
                    }));
                } catch (e) {
                    // Safe ignore
                }
            }
        }
    } catch (e) {
        console.warn('GitHub API rate limit or network issue:', e);
    }
}

/* ==========================================================================
   2. THEME CONTROLLER (SYNCED WITH WORKSPACE)
   ========================================================================== */
function initTheme() {
    const savedTheme = localStorage.getItem('deepresearch_theme') || 'dark';
    const isLight = savedTheme === 'light';
    document.documentElement.classList.toggle('light-mode', isLight);
    updateThemeToggleIcons(isLight);

    const themeBtn = document.getElementById('theme-toggle-btn');
    if (themeBtn) {
        themeBtn.addEventListener('click', () => {
            const currentlyLight = document.documentElement.classList.toggle('light-mode');
            localStorage.setItem('deepresearch_theme', currentlyLight ? 'light' : 'dark');
            updateThemeToggleIcons(currentlyLight);
        });
    }
}

function updateThemeToggleIcons(isLight) {
    const themeBtn = document.getElementById('theme-toggle-btn');
    if (!themeBtn) return;
    themeBtn.innerHTML = isLight 
        ? '<i data-lucide="sun" style="width: 17px; height: 17px;"></i>' 
        : '<i data-lucide="moon" style="width: 17px; height: 17px;"></i>';
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
        window.lucide.createIcons();
    }
}

/* ==========================================================================
   3. HERO WORKFLOW & OUTPUT INSPECTOR TABS
   ========================================================================== */
function initInspectorTabs() {
    const tabButtons = document.querySelectorAll('.inspector-tab-btn');
    const tabPanes = document.querySelectorAll('.tab-pane');

    tabButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const targetTab = btn.getAttribute('data-tab');

            tabButtons.forEach(b => b.classList.remove('active'));
            tabPanes.forEach(p => p.classList.remove('active'));

            btn.classList.add('active');
            const targetPane = document.getElementById(`tab-pane-${targetTab}`);
            if (targetPane) {
                targetPane.classList.add('active');
            }

            if (window.lucide && typeof window.lucide.createIcons === 'function') {
                window.lucide.createIcons();
            }
        });
    });
}

/* ==========================================================================
   3b. EXAMPLE GALLERY (REAL RESEARCH MODE OUTPUT)
   ========================================================================== */
let LANDING_EXAMPLES = [];

async function initExampleGallery() {
    const selectCards = document.querySelectorAll('.example-select-card');
    if (!selectCards.length) return;

    try {
        const response = await fetch('/assets/examples.json');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        LANDING_EXAMPLES = await response.json();
    } catch (e) {
        console.warn('Could not load landing examples.json, keeping static markup:', e);
        return;
    }

    if (!LANDING_EXAMPLES.length) return;

    selectCards.forEach(card => {
        card.addEventListener('click', () => {
            const example = LANDING_EXAMPLES.find(e => e.id === card.getAttribute('data-example-id'));
            if (example) renderExample(example, card);
        });
    });

    renderExample(LANDING_EXAMPLES[0], selectCards[0]);
}

/* Collapse markdown source to a plain-text teaser of at most `max` characters,
   trimmed at a word boundary. The landing card is a preview, not the full paper. */
function plainSnippet(md, max) {
    const text = String(md || '')
        .replace(/```[\s\S]*?```/g, ' ')
        .replace(/^\s*[-*_]{3,}\s*$/gm, '')            // horizontal rules
        .replace(/^\s*#{1,6}\s*(.+?)\s*$/gm, '$1. ')   // heading line -> "Heading. "
        .replace(/^\s*\*\*(.+?)\*\*\s*$/gm, '$1. ')    // bold-only line -> "Label. "
        .replace(/\*\*(.+?)\*\*/g, '$1')               // inline bold -> unwrap
        .replace(/[*_`>#]/g, '')
        .replace(/^\s*Literature Review:[^.]*\.\s*/i, '') // drop the outer review label
        .replace(/\s+/g, ' ')
        .replace(/\s+([.,;:])/g, '$1')
        .replace(/(\.\s*){2,}/g, '. ')
        .trim();
    if (text.length <= max) return text;
    const cut = text.slice(0, max);
    const sp = cut.lastIndexOf(' ');
    return (sp > 0 ? cut.slice(0, sp) : cut).replace(/[\s.,;:—-]+$/, '') + '…';
}

/* Shorten the "Indexed via …" tail of a paper meta line to a few flagship
   sources with pretty names, so it stays on one line in the mockup card. */
function prettifyMeta(meta) {
    const NAMES = {
        openalex: 'OpenAlex', semantic_scholar: 'Semantic Scholar', crossref: 'Crossref',
        pubmed: 'PubMed', arxiv: 'arXiv', europe_pmc: 'Europe PMC', doaj: 'DOAJ',
        datacite: 'DataCite', opencitations: 'OpenCitations', core: 'CORE', unpaywall: 'Unpaywall',
    };
    const ORDER = ['OpenAlex', 'Semantic Scholar', 'Crossref', 'PubMed', 'arXiv', 'Europe PMC', 'DOAJ', 'DataCite', 'OpenCitations'];
    return String(meta || '').replace(/Indexed via\s+(.+)$/i, (_, list) => {
        let sources = [...new Set(list.split(/,\s*/)
            .map(s => s.trim())
            .filter(s => s && !/fallback/i.test(s))
            .map(s => NAMES[s.toLowerCase()] || s.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())))];
        sources.sort((a, b) => {
            const ia = ORDER.indexOf(a), ib = ORDER.indexOf(b);
            return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
        });
        const shown = sources.slice(0, 5).join(', ');
        return 'Indexed via ' + (sources.length > 5 ? `${shown} +${sources.length - 5} more` : shown);
    });
}

function renderExample(example, activeCard) {
    document.querySelectorAll('.example-select-card').forEach(c => {
        const isActive = c === activeCard;
        c.classList.toggle('active', isActive);
        c.setAttribute('aria-selected', String(isActive));
    });

    const questionEl = document.getElementById('example-question-text');
    if (questionEl) questionEl.textContent = `"${example.question}"`;

    const discoveredEl = document.getElementById('metric-discovered');
    const screenedEl = document.getElementById('metric-screened');
    const includedEl = document.getElementById('metric-included');
    if (discoveredEl) discoveredEl.textContent = example.stats.discovered;
    if (screenedEl) screenedEl.textContent = example.stats.screened;
    if (includedEl) includedEl.textContent = example.stats.included;

    const prismaEl = document.getElementById('prisma-diagram-preview');
    if (prismaEl) {
        const p = example.prisma;
        prismaEl.innerHTML = `
            <div class="prisma-step-row">
                <div class="prisma-box">
                    <div class="prisma-box-title">1. Identification</div>
                    <div class="prisma-box-stat">${p.identification.stat}</div>
                    <div class="prisma-box-sub">${p.identification.sub}</div>
                </div>
                <div class="prisma-box prisma-box-excluded">
                    <div class="prisma-box-title">Deduplication</div>
                    <div class="prisma-box-stat">${p.dedup_excluded.stat}</div>
                    <div class="prisma-box-sub">${p.dedup_excluded.sub}</div>
                </div>
            </div>
            <div class="prisma-step-row">
                <div class="prisma-box">
                    <div class="prisma-box-title">2. Screening</div>
                    <div class="prisma-box-stat">${p.screening.stat}</div>
                    <div class="prisma-box-sub">${p.screening.sub}</div>
                </div>
                <div class="prisma-box prisma-box-excluded">
                    <div class="prisma-box-title">Irrelevant</div>
                    <div class="prisma-box-stat">${p.irrelevant_excluded.stat}</div>
                    <div class="prisma-box-sub">${p.irrelevant_excluded.sub}</div>
                </div>
            </div>
            <div class="prisma-step-row">
                <div class="prisma-box">
                    <div class="prisma-box-title">3. Included Corpus</div>
                    <div class="prisma-box-stat">${p.included.stat}</div>
                    <div class="prisma-box-sub">${p.included.sub}</div>
                </div>
            </div>
        `;
    }

    const tbody = document.getElementById('evidence-table-body');
    if (tbody) {
        tbody.innerHTML = example.evidence_rows.map(row => `
            <tr>
                <td><strong>${row.study}</strong></td>
                <td>${row.focus}</td>
                <td>${row.methodology}</td>
                <td>${row.finding}</td>
                <td><span class="evidence-tag">${row.level}</span></td>
            </tr>
        `).join('');
    }

    const titleEl = document.getElementById('paper-mock-title');
    const metaEl = document.getElementById('paper-mock-meta');
    const abstractEl = document.getElementById('paper-mock-abstract-text');
    const bodyEl = document.getElementById('paper-mock-body-text');
    const citationsEl = document.getElementById('paper-mock-citations-list');
    if (titleEl) titleEl.textContent = example.paper.title;
    if (metaEl) metaEl.textContent = prettifyMeta(example.paper.meta);
    if (abstractEl) abstractEl.textContent = plainSnippet(example.paper.abstract, 400);
    if (bodyEl) bodyEl.textContent = plainSnippet(example.paper.body, 260);
    if (citationsEl) {
        citationsEl.innerHTML = example.paper.citations.map(c => `<li>${c}</li>`).join('');
    }

    const runBtn = document.getElementById('run-example-btn');
    if (runBtn) {
        runBtn.href = `/app?mode=researchmode&q=${encodeURIComponent(example.question)}`;
    }

    if (window.lucide && typeof window.lucide.createIcons === 'function') {
        window.lucide.createIcons();
    }
}

/* ==========================================================================
   4. APPLE SMOOTH SCROLL FOR IN-PAGE ANCHORS
   ========================================================================== */
function initSmoothScroll() {
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            const targetId = this.getAttribute('href');
            if (!targetId || targetId === '#') return;
            const targetEl = document.querySelector(targetId);
            if (targetEl) {
                e.preventDefault();
                const headerOffset = 64;
                const elementPosition = targetEl.getBoundingClientRect().top;
                const offsetPosition = elementPosition + window.pageYOffset - headerOffset;

                window.scrollTo({
                    top: offsetPosition,
                    behavior: 'smooth'
                });
            }
        });
    });
}

/* ==========================================================================
   5. MOBILE NAVIGATION
   ========================================================================== */
function initMobileNav() {
    const toggleBtn = document.getElementById('mobile-nav-toggle');
    const navMenu = document.getElementById('nav-menu');

    if (toggleBtn && navMenu) {
        const updateToggleIcon = (isOpened) => {
            toggleBtn.setAttribute('aria-expanded', String(isOpened));
            toggleBtn.innerHTML = isOpened
                ? '<i data-lucide="x" style="width: 20px; height: 20px;"></i>'
                : '<i data-lucide="menu" style="width: 20px; height: 20px;"></i>';
            if (window.lucide && typeof window.lucide.createIcons === 'function') {
                window.lucide.createIcons();
            }
        };

        const closeMenu = () => {
            navMenu.classList.remove('mobile-open');
            updateToggleIcon(false);
        };

        toggleBtn.addEventListener('click', () => {
            const isOpened = navMenu.classList.toggle('mobile-open');
            updateToggleIcon(isOpened);
        });

        navMenu.querySelectorAll('.nav-link').forEach(link => {
            link.addEventListener('click', closeMenu);
        });

        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && navMenu.classList.contains('mobile-open')) {
                closeMenu();
                toggleBtn.focus();
            }
        });

        window.addEventListener('resize', () => {
            if (window.innerWidth > 960) closeMenu();
        });
    }
}

/* ==========================================================================
   6. APPLE INTERSECTION OBSERVER SCROLL REVEAL
   ========================================================================== */
function initScrollAnimations() {
    const revealElements = document.querySelectorAll('.apple-scroll-reveal');
    if (!revealElements.length) return;

    if (!('IntersectionObserver' in window)) {
        revealElements.forEach(el => el.classList.add('in-view'));
        return;
    }

    const observer = new IntersectionObserver((entries, obs) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('in-view');
                obs.unobserve(entry.target);
            }
        });
    }, {
        threshold: 0.12,
        rootMargin: '0px 0px -40px 0px'
    });

    revealElements.forEach(el => observer.observe(el));
}

/* ==========================================================================
   7. APPLE DYNAMIC NAVBAR SCROLL BEHAVIOR
   ========================================================================== */
function initNavbarScroll() {
    const header = document.querySelector('.landing-header');
    if (!header) return;

    let ticking = false;
    function checkScroll() {
        if (window.scrollY > 15) {
            header.classList.add('scrolled');
        } else {
            header.classList.remove('scrolled');
        }
        ticking = false;
    }

    window.addEventListener('scroll', () => {
        if (!ticking) {
            window.requestAnimationFrame(checkScroll);
            ticking = true;
        }
    }, { passive: true });

    // Initial check on load
    checkScroll();
}

/* ==========================================================================
   8. SPECULAR LIGHTING POINTER TRACKING
   ========================================================================== */
function initCardSpecularLighting() {
    const cards = document.querySelectorAll('.card-specular-glow');
    cards.forEach(card => {
        card.addEventListener('mousemove', (e) => {
            const rect = card.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            card.style.setProperty('--mouse-x', `${x}px`);
            card.style.setProperty('--mouse-y', `${y}px`);
        });
    });
}
