/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'hydro-bg': '#0B132B',
        'hydro-panel': '#1C2541',
        'hydro-accent': '#3A506B',
        'hydro-text': '#5BC0BE',
        'hydro-danger': '#FF595E',
        'hydro-warn': '#FFCA3A',
        'hydro-safe': '#8AC926',
      }
    },
  },
  plugins: [],
}
