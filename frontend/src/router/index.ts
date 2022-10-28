import { createRouter, createWebHistory } from "vue-router";
import { useUserStore } from "@/stores/user";
import HomeView from "../views/AboutView.vue";

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: "/",
      name: "home",
      component: HomeView,
    },
    {
      path: "/login",
      name: "login",
      component: () => import("../views/LoginView.vue"),
    },
    {
      path: "/samples",
      name: "samples",
      component: () => import("../views/SamplesView.vue"),
      beforeEnter: (to, from) => {
        const user = useUserStore();
        if (user.email.length === 0 && to.name !== "Login") {
          return { name: "login" };
        }
      },
    },
    {
      path: "/admin",
      name: "admin",
      component: () => import("../views/AdminView.vue"),
      beforeEnter: (to, from) => {
        const user = useUserStore();
        if (user.email.length === 0 && to.name !== "Login") {
          return { name: "login" };
        }
      },
    },
  ],
});

export default router;