import React, { createContext, useContext, useState, useEffect } from 'react';

const AuthContext = createContext(null);

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api/v1';

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem('krishnaflow_token'));
  const [user, setUser] = useState(null);
  const [locations, setLocations] = useState([]);
  const [loading, setLoading] = useState(true);

  // Fetch current user profile whenever token changes
  useEffect(() => {
    if (!token) {
      setUser(null);
      setLocations([]);
      setLoading(false);
      return;
    }

    const fetchMe = async () => {
      try {
        const resp = await fetch(`${API_BASE}/auth/me`, {
          headers: { Authorization: `Bearer ${token}` }
        });
        if (resp.ok) {
          const data = await resp.json();
          setUser(data.user);
          setLocations(data.locations || []);
        } else {
          // Token expired or invalid
          logout();
        }
      } catch (err) {
        console.error('Failed to fetch user profile:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchMe();
  }, [token]);

  const login = async (identifier, password) => {
    const resp = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ identifier, password })
    });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.detail || 'Login failed');
    }
    localStorage.setItem('krishnaflow_token', data.access_token);
    setToken(data.access_token);
    setUser(data.user);
    return data.user;
  };

  const register = async (userData) => {
    const resp = await fetch(`${API_BASE}/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(userData)
    });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.detail || 'Registration failed');
    }
    return data;
  };

  const verifyOtp = async (email, phoneOtp, emailOtp) => {
    const resp = await fetch(`${API_BASE}/auth/verify-otp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email,
        phone_otp: phoneOtp,
        email_otp: emailOtp
      })
    });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.detail || 'OTP Verification failed');
    }
    localStorage.setItem('krishnaflow_token', data.access_token);
    setToken(data.access_token);
    setUser(data.user);
    return data.user;
  };

  const resendOtp = async (email, channel = 'all') => {
    const resp = await fetch(`${API_BASE}/auth/resend-otp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, channel })
    });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.detail || 'Failed to resend OTP');
    }
    return data;
  };

  const logout = () => {
    localStorage.removeItem('krishnaflow_token');
    setToken(null);
    setUser(null);
    setLocations([]);
  };

  const refreshProfile = async () => {
    if (!token) return;
    try {
      const resp = await fetch(`${API_BASE}/auth/me`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (resp.ok) {
        const data = await resp.json();
        setUser(data.user);
        setLocations(data.locations || []);
      }
    } catch (err) {
      console.error('Failed to refresh profile:', err);
    }
  };

  return (
    <AuthContext.Provider
      value={{
        token,
        user,
        locations,
        loading,
        isAuthenticated: !!user,
        isAdmin: user?.role === 'admin',
        login,
        register,
        verifyOtp,
        resendOtp,
        logout,
        refreshProfile
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
