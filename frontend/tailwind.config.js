/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#092421",
        cream: "#f6f5ed",
        saffron: "#efad3b",
        leaf: "#087b64"
      },
      boxShadow: {
        soft: "0 12px 40px rgba(4, 38, 33, 0.10)"
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"]
      }
    }
  },
  plugins: []
};

