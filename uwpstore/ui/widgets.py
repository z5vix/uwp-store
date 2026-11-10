"""Reusable storefront widgets: product cards, rows, the spotlight carousel."""

from __future__ import annotations

from typing import Callable

from gi.repository import Adw, GLib, Gtk, Pango

from .. import catalog
from .util import load_image, stars

OpenProduct = Callable[[str, str], None]   # (product_id, title)


def logo_url(card: catalog.Card, size: int = 96) -> str | None:
    image = catalog.pick_image(card.images, ("logo", "tile", "BoxArt", "Poster"))
    return catalog.image_url(image, size * 2)


class AppCard(Gtk.Button):
    """A square tile: logo, title, price and rating."""

    def __init__(self, card: catalog.Card, open_product: OpenProduct, width: int = 150) -> None:
        super().__init__(halign=Gtk.Align.START, valign=Gtk.Align.START)
        self.card = card
        self.add_css_class("store-card")
        self.set_tooltip_text(card.title)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_size_request(width, -1)

        # a Gtk.Image keeps a fixed square size whatever the art's own size (a Picture asks for its natural size)
        art = Gtk.Image(pixel_size=width - 24, halign=Gtk.Align.CENTER, overflow=Gtk.Overflow.HIDDEN)
        art.add_css_class("store-art")
        image = catalog.pick_image(card.images, ("BoxArt", "Poster", "logo", "tile") if card.is_game else ("logo", "tile", "BoxArt"))
        load_image(art, catalog.image_url(image, width * 2))
        box.append(art)

        title = Gtk.Label(label=card.title, xalign=0, wrap=True, lines=2)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(16)
        title.add_css_class("heading")
        box.append(title)

        meta = Gtk.Box(spacing=6)
        price = Gtk.Label(label=card.price or "", xalign=0)
        price.add_css_class("caption")
        meta.append(price)
        if card.rating > 0:
            rating = Gtk.Label(label=stars(card.rating), xalign=1, hexpand=True)
            rating.add_css_class("caption")
            rating.add_css_class("dim-label")
            meta.append(rating)
        box.append(meta)
        self.set_child(box)
        self.connect("clicked", lambda *_: open_product(card.product_id, card.title))


class CardRow(Gtk.Box):
    """A titled, horizontally scrolling row of cards."""

    def __init__(self, title: str, cards: list[catalog.Card], open_product: OpenProduct,
                 on_more: Callable[[], None] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        header = Gtk.Box(spacing=12)
        label = Gtk.Label(label=title, xalign=0, hexpand=True)
        label.add_css_class("title-3")
        header.append(label)
        if on_more:
            more = Gtk.Button(label="See all")
            more.add_css_class("flat")
            more.connect("clicked", lambda *_: on_more())
            header.append(more)
        self.append(header)

        scroller = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER,
                                      hscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        scroller.add_css_class("store-row")
        strip = Gtk.Box(spacing=12)
        strip.set_margin_bottom(10)
        for card in cards:
            if card.is_product:
                strip.append(AppCard(card, open_product))
        scroller.set_child(strip)
        self.append(scroller)


class CardGrid(Adw.WrapBox if hasattr(Adw, "WrapBox") else Gtk.FlowBox):  # type: ignore[misc]
    def __init__(self, cards: list[catalog.Card], open_product: OpenProduct) -> None:
        super().__init__()
        if isinstance(self, Gtk.FlowBox):
            self.set_selection_mode(Gtk.SelectionMode.NONE)
            self.set_column_spacing(12)
            self.set_row_spacing(12)
        else:
            self.set_child_spacing(12)
            self.set_line_spacing(12)
        for card in cards:
            if card.is_product:
                self.append(AppCard(card, open_product))


class Spotlight(Gtk.Box):
    """The home page's hero: a carousel of wide promotional art that advances by itself."""

    def __init__(self, cards: list[catalog.Card], open_product: OpenProduct) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.carousel = Adw.Carousel(allow_long_swipes=False, spacing=12)
        self.carousel.set_size_request(-1, 320)
        for card in cards:
            if not card.is_product:
                continue
            image = catalog.pick_image(card.images, ("spotlight", "SuperHeroArt", "TitledHeroArt", "Poster", "BoxArt"), 1000)
            overlay = Gtk.Overlay()
            overlay.add_css_class("store-hero")
            picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, hexpand=True, can_shrink=True)
            load_image(picture, catalog.image_url(image, 1600))
            overlay.set_child(picture)
            shade = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.END)
            shade.add_css_class("store-hero-shade")
            title = Gtk.Label(label=card.title, xalign=0, wrap=True)
            title.add_css_class("title-1")
            shade.append(title)
            if card.description:
                text = Gtk.Label(label=card.description, xalign=0, wrap=True, lines=2,
                                 ellipsize=Pango.EllipsizeMode.END)
                shade.append(text)
            button = Gtk.Button(label=card.price or "View", halign=Gtk.Align.START)
            button.add_css_class("suggested-action")
            button.add_css_class("pill")
            button.connect("clicked", lambda _b, c=card: open_product(c.product_id, c.title))
            shade.append(button)
            overlay.add_overlay(shade)
            self.carousel.append(overlay)
        self.append(self.carousel)
        dots = Adw.CarouselIndicatorDots(carousel=self.carousel)
        self.append(dots)
        self._timer = GLib.timeout_add_seconds(7, self._advance)
        self.connect("unrealize", lambda *_: self._stop())

    def _stop(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _advance(self) -> bool:
        pages = self.carousel.get_n_pages()
        if pages > 1 and self.get_mapped():
            nxt = (round(self.carousel.get_position()) + 1) % pages
            self.carousel.scroll_to(self.carousel.get_nth_page(nxt), True)
        return GLib.SOURCE_CONTINUE


def loading(label: str = "Loading…") -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER,
                  halign=Gtk.Align.CENTER, vexpand=True)
    box.append(Adw.Spinner(width_request=32, height_request=32))
    text = Gtk.Label(label=label)
    text.add_css_class("dim-label")
    box.append(text)
    return box


def error_page(message: str, retry: Callable[[], None] | None = None) -> Gtk.Widget:
    page = Adw.StatusPage(icon_name="network-error-symbolic", title="Couldn't load this", description=message,
                          vexpand=True)
    if retry:
        button = Gtk.Button(label="Try again", halign=Gtk.Align.CENTER)
        button.add_css_class("pill")
        button.connect("clicked", lambda *_: retry())
        page.set_child(button)
    return page


def page_scroller(child: Gtk.Widget) -> Gtk.ScrolledWindow:
    clamp = Adw.Clamp(maximum_size=1400, tightening_threshold=1000, child=child)
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    scroller.set_child(clamp)
    return scroller
