import { describe, it, expect } from "vitest";

import { mount } from "@vue/test-utils";
import TitleComponent from "../TitleComponent.vue";

describe("HelloWorld", () => {
  it("renders properly", () => {
    const wrapper = mount(TitleComponent, { props: { msg: "Hello Vitest" } });
    expect(wrapper.text()).toContain("Hello Vitest");
  });
});