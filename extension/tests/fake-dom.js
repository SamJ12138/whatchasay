// The few DOM pieces content-scripts/overlay.js touches, so its real render path
// runs under node: elements with classes, dataset, children and text, a shadow
// root, and a ResizeObserver that never fires.
'use strict';

class FakeElement {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.className = '';
    this.textContent = '';
    this.isConnected = false;
    const el = this;
    this.classList = {
      add(c) { if (!this.contains(c)) el.className = (el.className + ' ' + c).trim(); },
      contains(c) { return el.className.split(/\s+/).includes(c); },
    };
  }
  appendChild(child) { this.children.push(child); child.isConnected = true; return child; }
  set innerHTML(html) { if (html === '') { for (const c of this.children) c.isConnected = false; this.children = []; } }
  attachShadow() { return new FakeElement('#shadow-root'); }
  querySelector(tag) { return this.children.find((c) => c.tagName === String(tag).toUpperCase()) || null; }
  addEventListener() {}
  remove() { this.isConnected = false; }
  focus() {}
}

function fakeDom() {
  return {
    document: { createElement: (tag) => new FakeElement(tag), body: new FakeElement('body') },
    ResizeObserver: class { observe() {} disconnect() {} },
  };
}

module.exports = { fakeDom, FakeElement };
