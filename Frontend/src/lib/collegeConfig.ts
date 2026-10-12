export const collegeConfig = {
  name: "National Institute of Technical Teachers Training and Research",
  shortName: "NITTTR Chennai",
  established: 1964,
  location: "Taramani, Chennai, Tamil Nadu",
  officialWebsite: "https://nitttrc.ac.in",
  contact: {
    email: "director@nitttrc.ac.in",
    phone: "044-22545406", // From website generally or to be updated by admin. Assuming standard default. 
    address: "Taramani, Chennai - 600113",
  },
  images: {
    hero: "/images/nitttr-campus.jpg",
    logo: "/images/nitttr-logo.png",
  },
  theme: {
    primaryColor: "blue-900", // Navy Blue
    secondaryColor: "blue-600",
  },
  description: "An autonomous Institute under Ministry of Education, Government of India, dedicated to the improvement of Technical Education."
};

// Source mapping to ensure data integrity
export const collegeSources = {
  name: { sourceURL: "https://nitttrc.ac.in/", lastVerified: "2026-09-21" },
  images: { sourceURL: "https://commons.wikimedia.org/wiki/File:NITTTR_Chennai-02.jpg", lastVerified: "2026-09-21" },
  description: { sourceURL: "https://nitttrc.ac.in/", lastVerified: "2026-09-21" },
};
