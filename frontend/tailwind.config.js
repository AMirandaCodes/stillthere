/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  safelist: ["animate-spin"],
  theme: {
    extend: {
      fontFamily: {
        serif: ["Georgia", "serif"],
      },
      colors: {
        brand: {
          50:  "#e6f6f7",
          300: "#6ba3be",
          500: "#0c959b",
          600: "#0a7075",
          700: "#274d60",
          800: "#032f30",
          900: "#031716",
        },
      },
    },
  },
  plugins: [],
};
