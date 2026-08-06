package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/jackc/pgx/v5/pgconn"
)

// Sentinel errors.
var (
	ErrNotFound       = errors.New("user not found")
	ErrDuplicateEmail = errors.New("email already registered")
)

// User represents a row in the users table.
type User struct {
	ID             string
	OrganisationID *string // nullable: user may not belong to an org yet
	Email          string
	PasswordHash   string
	DisplayName    string
	Role           string
	CreatedAt      time.Time
	UpdatedAt      time.Time
}

// UserRepository handles persistence operations for users.
type UserRepository struct {
	db *sql.DB
}

// NewUserRepository creates a new UserRepository backed by the given *sql.DB.
func NewUserRepository(db *sql.DB) *UserRepository {
	return &UserRepository{db: db}
}

// DB returns the underlying *sql.DB, used for transactions in handlers.
func (r *UserRepository) DB() *sql.DB {
	return r.db
}

// Create inserts a new user into the database.
// Returns ErrDuplicateEmail if the email is already taken.
func (r *UserRepository) Create(ctx context.Context, u *User) error {
	_, err := r.db.ExecContext(ctx,
		`INSERT INTO users (id, organisation_id, email, password_hash, display_name, role, created_at, updated_at)
		 VALUES ($1, $2, $3, $4, $5, $6, $7, $8)`,
		u.ID, u.OrganisationID, u.Email, u.PasswordHash, u.DisplayName, u.Role, u.CreatedAt, u.UpdatedAt,
	)
	if err != nil {
		var pgErr *pgconn.PgError
		if errors.As(err, &pgErr) && pgErr.Code == "23505" {
			return ErrDuplicateEmail
		}
		return err
	}
	return nil
}

// GetByEmail fetches a user by email address.
// Returns ErrNotFound if no row matches.
func (r *UserRepository) GetByEmail(ctx context.Context, email string) (*User, error) {
	u := &User{}
	var orgID *string

	err := r.db.QueryRowContext(ctx,
		`SELECT id, organisation_id, email, password_hash, display_name, role, created_at, updated_at
		 FROM users WHERE email = $1`,
		email,
	).Scan(&u.ID, &orgID, &u.Email, &u.PasswordHash, &u.DisplayName, &u.Role, &u.CreatedAt, &u.UpdatedAt)

	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	u.OrganisationID = orgID
	return u, nil
}
